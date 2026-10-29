package nl.zekerzichtbaar.narrowcasting.setup

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.BluetoothStatusCodes
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.location.LocationManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.ParcelUuid
import android.os.SystemClock
import androidx.core.content.ContextCompat
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred

/**
 * GATT central for the Pi's management service (docs/ble-management.md). Android runs one GATT
 * operation at a time and drops the rest, so every operation goes through [ops].
 */
@SuppressLint("MissingPermission")
class ManageBleTransport(context: Context) : ManageTransport {
    override var onEvent: ((ManageTransportEvent) -> Unit)? = null
    override val usesBluetooth = true

    private val context = context.applicationContext
    private val main = Handler(Looper.getMainLooper())
    private val adapter: BluetoothAdapter? = this.context.getSystemService(BluetoothManager::class.java)?.adapter

    private var localName: String? = null
    private var scanWaiter: CompletableDeferred<List<ManageCandidate>>? = null
    private val found = LinkedHashMap<String, Pair<BluetoothDevice, Int>>()
    private var searching = false
    private var scanning = false
    private var windowOpen = false
    private var receiverRegistered = false
    private val scanStarts = ArrayDeque<Long>()

    private var gatt: BluetoothGatt? = null
    private var connectWaiter: CompletableDeferred<Unit>? = null
    private var linked = false
    private var closing = false
    private var sessionChar: BluetoothGattCharacteristic? = null
    private var requestChar: BluetoothGattCharacteristic? = null
    private var responseChar: BluetoothGattCharacteristic? = null
    private val ops = ArrayDeque<Op>()
    private var current: Op? = null

    private val notFound = Runnable {
        if (scanWaiter != null && found.isEmpty()) emit(ManageTransportEvent.NotFoundYet)
    }
    private val windowClosed = Runnable { finishScan() }
    private val scanAgain = Runnable { startScan() }
    private val connectTimedOut = Runnable { abortConnect(ManageLinkException.Timeout()) }
    private val opTimedOut = Runnable {
        if (current == Op.Mtu) opDone() else if (current != null) lost()
    }

    private sealed interface Op {
        data object Mtu : Op
        data object Discover : Op
        class Notify(val characteristic: BluetoothGattCharacteristic) : Op

        /** `result` null: a RESPONSE read, answered with an event. */
        class Read(val characteristic: BluetoothGattCharacteristic, val result: CompletableDeferred<ByteArray>?) : Op
        class Write(val characteristic: BluetoothGattCharacteristic, val value: ByteArray, val result: CompletableDeferred<Unit>) : Op
    }

    override suspend fun candidates(localName: String): List<ManageCandidate> {
        this.localName = localName
        found.clear()
        scanWaiter?.completeExceptionally(CancellationException("superseded"))
        val waiter = CompletableDeferred<List<ManageCandidate>>()
        scanWaiter = waiter
        registerReceiver()
        evaluate()
        try {
            return waiter.await()
        } finally {
            if (scanWaiter === waiter) {
                scanWaiter = null
                endScan()
            }
        }
    }

    override suspend fun connect(candidate: ManageCandidate) {
        val adapter = adapter?.takeIf { it.isEnabled } ?: throw ManageLinkException.Disconnected()
        val device = found[candidate.id]?.first
            ?: runCatching { adapter.getRemoteDevice(candidate.id) }.getOrNull()
            ?: throw ManageLinkException.Disconnected()
        closeGatt()
        resetCharacteristics()
        closing = false
        linked = false
        val waiter = CompletableDeferred<Unit>()
        connectWaiter = waiter
        main.postDelayed(connectTimedOut, CONNECT_TIMEOUT_MS)
        gatt = try {
            device.connectGatt(context, false, gattCallback, BluetoothDevice.TRANSPORT_LE)
        } catch (e: SecurityException) {
            null
        }
        if (gatt == null) abortConnect(ManageLinkException.Disconnected())
        try {
            waiter.await()
        } catch (e: CancellationException) {
            if (connectWaiter === waiter) abortConnect(ManageLinkException.Disconnected())
            throw e
        }
    }

    override suspend fun writeSession(value: ByteArray) = write(sessionChar, value)

    override suspend fun readSession(): ByteArray {
        val characteristic = ready(sessionChar)
        val result = CompletableDeferred<ByteArray>()
        enqueue(Op.Read(characteristic, result))
        return result.await()
    }

    override suspend fun writeRequest(frame: ByteArray) = write(requestChar, frame)

    override fun readResponse() {
        if (gatt == null || !linked) return
        responseChar?.let { enqueue(Op.Read(it, null)) }
    }

    override fun disconnect() {
        closing = true
        lost()
    }

    override fun stop() {
        endScan()
        main.removeCallbacksAndMessages(null)
        scanWaiter?.completeExceptionally(CancellationException("stopped"))
        scanWaiter = null
        disconnect()
        unregisterReceiver()
    }

    override fun recheck() = evaluate()

    private fun evaluate() {
        if (scanWaiter == null) return
        val adapter = adapter
        val problem = when {
            adapter == null || !context.packageManager.hasSystemFeature(PackageManager.FEATURE_BLUETOOTH_LE) ->
                BluetoothProblem.UNSUPPORTED
            !BluetoothAccess.granted(context) -> BluetoothProblem.UNAUTHORIZED
            !adapter.isEnabled -> BluetoothProblem.OFF
            BluetoothAccess.needsLocation && !BluetoothAccess.locationEnabled(context) -> BluetoothProblem.LOCATION_OFF
            else -> null
        }
        if (problem != null) {
            stopScan()
            searching = false
            main.removeCallbacks(notFound)
            main.removeCallbacks(scanAgain)
            emit(ManageTransportEvent.Bluetooth(problem))
        } else if (!searching) {
            beginScan()
        }
    }

    private fun beginScan() {
        searching = true
        emit(ManageTransportEvent.Searching)
        main.removeCallbacks(notFound)
        main.postDelayed(notFound, NOT_FOUND_AFTER_MS)
        startScan()
    }

    // Android silently stops delivering results to an app that starts more than 5 scans in 30 s.
    private fun startScan() {
        main.removeCallbacks(scanAgain)
        if (scanWaiter == null || !searching || scanning) return
        val scanner = adapter?.takeIf { it.isEnabled }?.bluetoothLeScanner ?: return
        val now = SystemClock.elapsedRealtime()
        while (scanStarts.isNotEmpty() && now - scanStarts.first() > SCAN_WINDOW_MS) scanStarts.removeFirst()
        if (scanStarts.size >= MAX_SCANS_PER_WINDOW) {
            main.postDelayed(scanAgain, SCAN_WINDOW_MS - (now - scanStarts.first()) + 250)
            return
        }
        scanStarts.addLast(now)
        // Either service: a Pi in its setup window advertises the setup UUID but serves management too.
        val filters = listOf(ManageProtocol.serviceUuid, SetupProtocol.serviceUuid).map {
            ScanFilter.Builder().setServiceUuid(ParcelUuid(it)).build()
        }
        val settings = ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build()
        try {
            scanner.startScan(filters, settings, scanCallback)
            scanning = true
        } catch (e: SecurityException) {
            emit(ManageTransportEvent.Bluetooth(BluetoothProblem.UNAUTHORIZED))
        }
    }

    private fun stopScan() {
        if (!scanning) return
        scanning = false
        try {
            adapter?.takeIf { it.isEnabled }?.bluetoothLeScanner?.stopScan(scanCallback)
        } catch (_: SecurityException) {
        } catch (_: IllegalStateException) {
        }
    }

    private fun endScan() {
        stopScan()
        searching = false
        windowOpen = false
        main.removeCallbacks(notFound)
        main.removeCallbacks(scanAgain)
        main.removeCallbacks(windowClosed)
    }

    private fun discovered(result: ScanResult) {
        val localName = localName ?: return
        if (scanWaiter == null || !scanning) return
        // The advertised local name, not BluetoothDevice.name: Android caches GAP names.
        val name = result.scanRecord?.deviceName ?: return
        if (!name.equals(localName, ignoreCase = true)) return
        val rssi = if (result.rssi == 127) -127 else result.rssi
        val address = result.device.address
        found[address] = result.device to maxOf(found[address]?.second ?: -127, rssi)
        if (windowOpen) return
        windowOpen = true
        main.postDelayed(windowClosed, CANDIDATE_WINDOW_MS)
    }

    private fun finishScan() {
        val waiter = scanWaiter ?: return
        endScan()
        scanWaiter = null
        waiter.complete(found.map { (id, entry) -> ManageCandidate(id, entry.second) }.sortedByDescending { it.rssi })
    }

    private fun ready(characteristic: BluetoothGattCharacteristic?): BluetoothGattCharacteristic {
        if (gatt == null || !linked || characteristic == null) throw ManageLinkException.Disconnected()
        return characteristic
    }

    private suspend fun write(characteristic: BluetoothGattCharacteristic?, value: ByteArray) {
        val target = ready(characteristic)
        val result = CompletableDeferred<Unit>()
        enqueue(Op.Write(target, value, result))
        result.await()
    }

    private fun connectedState() {
        if (connectWaiter == null) return
        // Before anything else: a REQUEST frame can be ~150 bytes, the default MTU carries 20.
        enqueue(Op.Mtu)
        enqueue(Op.Discover)
    }

    private fun servicesDiscovered(gatt: BluetoothGatt, status: Int) {
        if (connectWaiter == null) return
        if (status != BluetoothGatt.GATT_SUCCESS) return abortConnect(ManageLinkException.Disconnected())
        val service = gatt.getService(ManageProtocol.serviceUuid)
        val session = service?.getCharacteristic(ManageProtocol.sessionUuid)
        val request = service?.getCharacteristic(ManageProtocol.requestUuid)
        val response = service?.getCharacteristic(ManageProtocol.responseUuid)
        if (session == null || request == null || response == null) {
            return abortConnect(ManageLinkException.NotManagement())
        }
        sessionChar = session
        requestChar = request
        responseChar = response
        enqueue(Op.Notify(response))
    }

    private fun subscribed(status: Int) {
        val waiter = connectWaiter ?: return
        val error = linkError(status)
        if (error != null) return abortConnect(error)
        main.removeCallbacks(connectTimedOut)
        connectWaiter = null
        linked = true
        waiter.complete(Unit)
    }

    private fun abortConnect(error: ManageLinkException) {
        main.removeCallbacks(connectTimedOut)
        val waiter = connectWaiter
        connectWaiter = null
        closeGatt()
        resetCharacteristics()
        linked = false
        waiter?.completeExceptionally(error)
    }

    private fun lost() {
        val wasLinked = linked && !closing
        main.removeCallbacks(connectTimedOut)
        val waiter = connectWaiter
        connectWaiter = null
        closeGatt()
        resetCharacteristics()
        linked = false
        waiter?.completeExceptionally(ManageLinkException.Disconnected())
        if (wasLinked) emit(ManageTransportEvent.Disconnected)
    }

    private fun closeGatt() {
        val pending = listOfNotNull(current) + ops
        ops.clear()
        current = null
        main.removeCallbacks(opTimedOut)
        gatt?.let {
            try {
                it.disconnect()
                it.close()
            } catch (_: SecurityException) {
            }
        }
        gatt = null
        pending.forEach { op ->
            when (op) {
                is Op.Read -> op.result?.completeExceptionally(ManageLinkException.Disconnected())
                is Op.Write -> op.result.completeExceptionally(ManageLinkException.Disconnected())
                else -> Unit
            }
        }
    }

    private fun resetCharacteristics() {
        sessionChar = null
        requestChar = null
        responseChar = null
    }

    private fun enqueue(op: Op) {
        ops.addLast(op)
        next()
    }

    private fun next() {
        if (current != null) return
        val gatt = gatt ?: return
        val op = ops.removeFirstOrNull() ?: return
        current = op
        val started = try {
            run(gatt, op)
        } catch (e: SecurityException) {
            false
        }
        when {
            started -> main.postDelayed(opTimedOut, OP_TIMEOUT_MS)
            op == Op.Mtu -> opDone()
            else -> lost()
        }
    }

    private fun opDone() {
        current = null
        main.removeCallbacks(opTimedOut)
        next()
    }

    private fun run(gatt: BluetoothGatt, op: Op): Boolean = when (op) {
        Op.Mtu -> gatt.requestMtu(MTU)
        Op.Discover -> gatt.discoverServices()
        is Op.Read -> gatt.readCharacteristic(op.characteristic)
        is Op.Notify -> {
            val cccd = op.characteristic.getDescriptor(CCCD)
            cccd != null && gatt.setCharacteristicNotification(op.characteristic, true) &&
                writeDescriptor(gatt, cccd, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)
        }
        is Op.Write -> writeCharacteristic(gatt, op.characteristic, op.value)
    }

    @Suppress("DEPRECATION")
    private fun writeDescriptor(gatt: BluetoothGatt, descriptor: BluetoothGattDescriptor, value: ByteArray): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            gatt.writeDescriptor(descriptor, value) == BluetoothStatusCodes.SUCCESS
        } else {
            descriptor.value = value
            gatt.writeDescriptor(descriptor)
        }

    @Suppress("DEPRECATION")
    private fun writeCharacteristic(gatt: BluetoothGatt, c: BluetoothGattCharacteristic, value: ByteArray): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            gatt.writeCharacteristic(c, value, BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT) == BluetoothStatusCodes.SUCCESS
        } else {
            c.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
            c.value = value
            gatt.writeCharacteristic(c)
        }

    private fun linkError(status: Int): ManageLinkException? = when (status) {
        BluetoothGatt.GATT_SUCCESS -> null
        BluetoothGatt.GATT_INSUFFICIENT_AUTHORIZATION -> ManageLinkException.Rejected()
        ATT_UNLIKELY_ERROR -> ManageLinkException.Malformed()
        GATT_ERROR, BluetoothGatt.GATT_FAILURE -> ManageLinkException.Disconnected()
        else -> ManageLinkException.Failed("ATT 0x%02x".format(status))
    }

    private fun emit(event: ManageTransportEvent) {
        onEvent?.invoke(event)
    }

    private fun onMain(block: () -> Unit) {
        if (Looper.myLooper() == Looper.getMainLooper()) block() else main.post(block)
    }

    private val scanCallback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) = onMain { discovered(result) }

        override fun onScanFailed(errorCode: Int) = onMain {
            scanning = false
            main.postDelayed(scanAgain, RETRY_DELAY_MS)
        }
    }

    private val gattCallback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) = onMain {
            if (g != gatt) {
                g.close()
            } else if (status == BluetoothGatt.GATT_SUCCESS && newState == BluetoothProfile.STATE_CONNECTED) {
                connectedState()
            } else {
                lost()
            }
        }

        override fun onMtuChanged(g: BluetoothGatt, mtu: Int, status: Int) = onMain {
            if (g == gatt && current == Op.Mtu) opDone()
        }

        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) = onMain {
            if (g != gatt) return@onMain
            if (current == Op.Discover) opDone()
            servicesDiscovered(g, status)
        }

        override fun onServiceChanged(g: BluetoothGatt) = onMain {
            if (g != gatt) return@onMain
            if (connectWaiter != null) {
                resetCharacteristics()
                enqueue(Op.Discover)
            } else {
                lost()
            }
        }

        override fun onDescriptorWrite(g: BluetoothGatt, descriptor: BluetoothGattDescriptor, status: Int) = onMain {
            if (g != gatt || current !is Op.Notify) return@onMain
            opDone()
            subscribed(status)
        }

        override fun onCharacteristicRead(g: BluetoothGatt, c: BluetoothGattCharacteristic, value: ByteArray, status: Int) =
            read(g, value, status)

        @Deprecated("Android 12L and older")
        override fun onCharacteristicRead(g: BluetoothGatt, c: BluetoothGattCharacteristic, status: Int) {
            @Suppress("DEPRECATION")
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) read(g, c.value?.copyOf() ?: ByteArray(0), status)
        }

        override fun onCharacteristicWrite(g: BluetoothGatt, c: BluetoothGattCharacteristic, status: Int) = onMain {
            if (g != gatt) return@onMain
            val op = current as? Op.Write ?: return@onMain
            opDone()
            val error = linkError(status)
            if (error == null) op.result.complete(Unit) else op.result.completeExceptionally(error)
        }

        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic, value: ByteArray) =
            changed(g, c.uuid, value)

        @Deprecated("Android 12L and older")
        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic) {
            @Suppress("DEPRECATION")
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) changed(g, c.uuid, c.value?.copyOf() ?: ByteArray(0))
        }

        private fun read(g: BluetoothGatt, value: ByteArray, status: Int) = onMain {
            if (g != gatt) return@onMain
            val op = current as? Op.Read ?: return@onMain
            opDone()
            val result = op.result
            val error = linkError(status)
            when {
                result == null -> if (error == null) emit(ManageTransportEvent.Response(value, read = true))
                error != null -> result.completeExceptionally(error)
                else -> result.complete(value)
            }
        }

        private fun changed(g: BluetoothGatt, uuid: UUID, value: ByteArray) = onMain {
            if (g == gatt && linked && uuid == ManageProtocol.responseUuid) {
                emit(ManageTransportEvent.Response(value, read = false))
            }
        }
    }

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(c: Context, intent: Intent) {
            if (intent.action == BluetoothAdapter.ACTION_STATE_CHANGED) {
                when (intent.getIntExtra(BluetoothAdapter.EXTRA_STATE, BluetoothAdapter.ERROR)) {
                    BluetoothAdapter.STATE_OFF -> {
                        scanning = false
                        searching = false
                        if (gatt != null) lost()
                        evaluate()
                    }
                    BluetoothAdapter.STATE_ON -> evaluate()
                }
            } else {
                evaluate()
            }
        }
    }

    private fun registerReceiver() {
        if (receiverRegistered) return
        val filter = IntentFilter().apply {
            addAction(BluetoothAdapter.ACTION_STATE_CHANGED)
            if (BluetoothAccess.needsLocation) addAction(LocationManager.PROVIDERS_CHANGED_ACTION)
        }
        ContextCompat.registerReceiver(context, receiver, filter, ContextCompat.RECEIVER_NOT_EXPORTED)
        receiverRegistered = true
    }

    private fun unregisterReceiver() {
        if (!receiverRegistered) return
        context.unregisterReceiver(receiver)
        receiverRegistered = false
    }

    private companion object {
        const val MTU = 517
        const val NOT_FOUND_AFTER_MS = 20_000L
        const val CANDIDATE_WINDOW_MS = 2_000L
        const val CONNECT_TIMEOUT_MS = 15_000L
        const val OP_TIMEOUT_MS = 10_000L
        const val RETRY_DELAY_MS = 1_000L
        const val SCAN_WINDOW_MS = 30_000L
        const val MAX_SCANS_PER_WINDOW = 4
        const val ATT_UNLIKELY_ERROR = 0x0e
        const val GATT_ERROR = 133
        val CCCD: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
    }
}
