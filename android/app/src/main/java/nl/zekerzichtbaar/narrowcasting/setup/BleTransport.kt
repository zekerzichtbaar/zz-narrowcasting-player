package nl.zekerzichtbaar.narrowcasting.setup

import android.Manifest
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
import androidx.core.location.LocationManagerCompat
import java.util.UUID

/** What Android needs before an app may scan for and connect to the screen. */
object BluetoothAccess {
    val permissions: Array<String> =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
        } else {
            arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)
        }

    /** Up to Android 11, BLE scans return nothing while Location is switched off. */
    val needsLocation = Build.VERSION.SDK_INT < Build.VERSION_CODES.S

    fun granted(context: Context) = permissions.all {
        ContextCompat.checkSelfPermission(context, it) == PackageManager.PERMISSION_GRANTED
    }

    fun locationEnabled(context: Context) =
        context.getSystemService(LocationManager::class.java)?.let(LocationManagerCompat::isLocationEnabled) ?: false
}

/**
 * GATT central for the Pi's wifi-setup service (docs/ble-setup.md). Android runs one GATT operation
 * at a time and drops the rest, so every operation goes through [ops].
 */
@SuppressLint("MissingPermission")
class BleTransport(context: Context) : SetupTransport {
    override var onEvent: ((TransportEvent) -> Unit)? = null
    override var autoReconnect = true
    override val usesBluetooth = true

    private val context = context.applicationContext
    private val main = Handler(Looper.getMainLooper())
    private val adapter: BluetoothAdapter? = this.context.getSystemService(BluetoothManager::class.java)?.adapter

    private var link: SetupLink? = null
    private var stopped = false
    private var scanning = false
    private var receiverRegistered = false
    private var gatt: BluetoothGatt? = null
    private var statusChar: BluetoothGattCharacteristic? = null
    private var networksChar: BluetoothGattCharacteristic? = null
    private var commandChar: BluetoothGattCharacteristic? = null
    private var lastScanId: Int? = null
    private val ops = ArrayDeque<Op>()
    private var current: Op? = null
    private val scanStarts = ArrayDeque<Long>()

    private val notFound = Runnable { if (gatt == null && !stopped) emit(TransportEvent.NotFoundYet) }
    private val opTimedOut = Runnable { current?.let(::opFailed) }
    private val scanAgain = Runnable { startScan() }
    private val evaluateAgain = Runnable { evaluate() }

    private sealed interface Op {
        data object Mtu : Op
        data object Discover : Op
        class Notify(val characteristic: BluetoothGattCharacteristic) : Op
        class Read(val characteristic: BluetoothGattCharacteristic) : Op
        class Write(val characteristic: BluetoothGattCharacteristic, val value: ByteArray) : Op
    }

    override fun start(link: SetupLink) {
        this.link = link
        stopped = false
        registerReceiver()
        evaluate()
    }

    override fun send(frame: ByteArray) {
        val command = commandChar
        if (gatt == null || command == null) {
            emit(TransportEvent.WriteFailed("Geen verbinding met het scherm. Even geduld, er wordt opnieuw verbonden."))
            return
        }
        enqueue(Op.Write(command, frame))
    }

    override fun stop() {
        stopped = true
        main.removeCallbacks(notFound)
        main.removeCallbacks(scanAgain)
        main.removeCallbacks(evaluateAgain)
        stopScan()
        closeGatt()
        unregisterReceiver()
    }

    private fun evaluate() {
        if (stopped || link == null) return
        val adapter = adapter
        val problem = when {
            adapter == null || !context.packageManager.hasSystemFeature(PackageManager.FEATURE_BLUETOOTH_LE) ->
                BluetoothProblem.UNSUPPORTED
            !BluetoothAccess.granted(context) -> BluetoothProblem.UNAUTHORIZED
            !adapter.isEnabled -> BluetoothProblem.OFF
            BluetoothAccess.needsLocation && gatt == null && !BluetoothAccess.locationEnabled(context) ->
                BluetoothProblem.LOCATION_OFF
            else -> null
        }
        if (problem != null) {
            stopScan()
            main.removeCallbacks(notFound)
            emit(TransportEvent.Bluetooth(problem))
        } else if (gatt == null && !scanning && autoReconnect) {
            beginScan()
        }
    }

    private fun beginScan() {
        resetCharacteristics()
        emit(TransportEvent.Searching)
        main.removeCallbacks(notFound)
        main.postDelayed(notFound, NOT_FOUND_AFTER_MS)
        startScan()
    }

    // Android silently stops delivering results to an app that starts more than 5 scans in 30 s.
    private fun startScan() {
        main.removeCallbacks(scanAgain)
        if (stopped || scanning || gatt != null) return
        val scanner = adapter?.takeIf { it.isEnabled }?.bluetoothLeScanner ?: return
        val now = SystemClock.elapsedRealtime()
        while (scanStarts.isNotEmpty() && now - scanStarts.first() > SCAN_WINDOW_MS) scanStarts.removeFirst()
        if (scanStarts.size >= MAX_SCANS_PER_WINDOW) {
            main.postDelayed(scanAgain, SCAN_WINDOW_MS - (now - scanStarts.first()) + 250)
            return
        }
        scanStarts.addLast(now)
        val filter = ScanFilter.Builder().setServiceUuid(ParcelUuid(SetupProtocol.serviceUuid)).build()
        val settings = ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build()
        try {
            scanner.startScan(listOf(filter), settings, scanCallback)
            scanning = true
        } catch (e: SecurityException) {
            emit(TransportEvent.Bluetooth(BluetoothProblem.UNAUTHORIZED))
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

    private fun discovered(result: ScanResult) {
        val link = link ?: return
        if (!scanning || gatt != null) return
        // The advertised local name, not BluetoothDevice.name: Android caches GAP names.
        val name = result.scanRecord?.deviceName ?: return
        if (!name.equals(link.localName, ignoreCase = true)) return
        stopScan()
        main.removeCallbacks(notFound)
        emit(TransportEvent.Connecting)
        gatt = result.device.connectGatt(context, false, gattCallback, BluetoothDevice.TRANSPORT_LE)
        if (gatt == null) lost()
    }

    private fun connected() {
        resetCharacteristics()
        // Before anything else: a COMMAND frame is ~130 bytes, the default MTU carries 20.
        enqueue(Op.Mtu)
        enqueue(Op.Discover)
    }

    private fun servicesDiscovered(gatt: BluetoothGatt, status: Int) {
        val service = gatt.getService(SetupProtocol.serviceUuid)
        val statusChar = service?.getCharacteristic(SetupProtocol.statusUuid)
        networksChar = service?.getCharacteristic(SetupProtocol.networksUuid)
        commandChar = service?.getCharacteristic(SetupProtocol.commandUuid)
        if (status != BluetoothGatt.GATT_SUCCESS || statusChar == null || networksChar == null || commandChar == null) {
            lost()
            return
        }
        this.statusChar = statusChar
        enqueue(Op.Notify(statusChar))
        enqueue(Op.Read(statusChar))
        emit(TransportEvent.Connected)
    }

    private fun lost() {
        closeGatt()
        resetCharacteristics()
        emit(TransportEvent.Disconnected)
        if (autoReconnect && !stopped) {
            main.removeCallbacks(evaluateAgain)
            main.postDelayed(evaluateAgain, RECONNECT_DELAY_MS)
        }
    }

    private fun closeGatt() {
        ops.clear()
        current = null
        main.removeCallbacks(opTimedOut)
        gatt?.let {
            it.disconnect()
            it.close()
        }
        gatt = null
    }

    private fun resetCharacteristics() {
        statusChar = null
        networksChar = null
        commandChar = null
        lastScanId = null
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
        if (started) main.postDelayed(opTimedOut, OP_TIMEOUT_MS) else opFailed(op)
    }

    private fun opDone() {
        current = null
        main.removeCallbacks(opTimedOut)
        next()
    }

    private fun opFailed(op: Op) {
        when (op) {
            Op.Mtu -> opDone()
            is Op.Write -> {
                opDone()
                emit(TransportEvent.WriteFailed("Versturen naar het scherm mislukt. Probeer het opnieuw."))
            }
            else -> lost()
        }
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

    private fun handleValue(uuid: UUID, value: ByteArray, fromRead: Boolean) {
        when (uuid) {
            SetupProtocol.statusUuid -> {
                val status = SetupStatus.decode(value)
                if (status == null) {
                    // A notification cut off at the MTU: the read returns the whole value.
                    if (!fromRead) statusChar?.let { enqueue(Op.Read(it)) }
                    return
                }
                val networks = networksChar
                if (status.scan != lastScanId && networks != null) {
                    lastScanId = status.scan
                    enqueue(Op.Read(networks))
                }
                emit(TransportEvent.Status(status))
            }
            SetupProtocol.networksUuid -> WifiNetwork.decodeList(value)?.let { emit(TransportEvent.Networks(it)) }
        }
    }

    private fun handleWrite(status: Int) {
        when (status) {
            BluetoothGatt.GATT_SUCCESS -> Unit
            BluetoothGatt.GATT_INSUFFICIENT_AUTHORIZATION -> emit(TransportEvent.WriteExpired)
            else -> emit(TransportEvent.WriteFailed("Het scherm weigerde de opdracht (fout ${"0x%02x".format(status)})."))
        }
    }

    private fun emit(event: TransportEvent) {
        onEvent?.invoke(event)
    }

    private fun onMain(block: () -> Unit) {
        if (Looper.myLooper() == Looper.getMainLooper()) block() else main.post(block)
    }

    private val scanCallback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) = onMain { discovered(result) }

        override fun onScanFailed(errorCode: Int) = onMain {
            scanning = false
            main.postDelayed(scanAgain, RECONNECT_DELAY_MS)
        }
    }

    private val gattCallback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) = onMain {
            if (g != gatt) {
                g.close()
            } else if (status == BluetoothGatt.GATT_SUCCESS && newState == BluetoothProfile.STATE_CONNECTED) {
                connected()
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
            resetCharacteristics()
            enqueue(Op.Discover)
        }

        override fun onDescriptorWrite(g: BluetoothGatt, descriptor: BluetoothGattDescriptor, status: Int) = onMain {
            if (g == gatt && current is Op.Notify) opDone()
        }

        override fun onCharacteristicRead(g: BluetoothGatt, c: BluetoothGattCharacteristic, value: ByteArray, status: Int) =
            read(g, c.uuid, value, status)

        @Deprecated("Android 12L and older")
        override fun onCharacteristicRead(g: BluetoothGatt, c: BluetoothGattCharacteristic, status: Int) {
            @Suppress("DEPRECATION")
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) read(g, c.uuid, c.value?.copyOf() ?: ByteArray(0), status)
        }

        override fun onCharacteristicWrite(g: BluetoothGatt, c: BluetoothGattCharacteristic, status: Int) = onMain {
            if (g != gatt || current !is Op.Write) return@onMain
            opDone()
            handleWrite(status)
        }

        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic, value: ByteArray) =
            changed(g, c.uuid, value)

        @Deprecated("Android 12L and older")
        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic) {
            @Suppress("DEPRECATION")
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) changed(g, c.uuid, c.value?.copyOf() ?: ByteArray(0))
        }

        private fun read(g: BluetoothGatt, uuid: UUID, value: ByteArray, status: Int) = onMain {
            if (g != gatt || current !is Op.Read) return@onMain
            opDone()
            if (status == BluetoothGatt.GATT_SUCCESS) handleValue(uuid, value, fromRead = true)
        }

        private fun changed(g: BluetoothGatt, uuid: UUID, value: ByteArray) = onMain {
            if (g == gatt) handleValue(uuid, value, fromRead = false)
        }
    }

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(c: Context, intent: Intent) {
            if (intent.action == BluetoothAdapter.ACTION_STATE_CHANGED) {
                when (intent.getIntExtra(BluetoothAdapter.EXTRA_STATE, BluetoothAdapter.ERROR)) {
                    BluetoothAdapter.STATE_OFF -> {
                        scanning = false
                        if (gatt != null) lost() else evaluate()
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
        const val OP_TIMEOUT_MS = 10_000L
        const val RECONNECT_DELAY_MS = 1_000L
        const val SCAN_WINDOW_MS = 30_000L
        const val MAX_SCANS_PER_WINDOW = 4
        val CCCD: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
    }
}
