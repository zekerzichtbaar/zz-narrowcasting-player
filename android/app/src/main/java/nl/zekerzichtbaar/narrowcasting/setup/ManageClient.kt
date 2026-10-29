package nl.zekerzichtbaar.narrowcasting.setup

import kotlin.coroutines.cancellation.CancellationException
import kotlin.time.Duration
import kotlin.time.Duration.Companion.milliseconds
import kotlin.time.Duration.Companion.seconds
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

sealed interface ManagePhase {
    data class Bluetooth(val problem: BluetoothProblem) : ManagePhase
    data class Searching(val slow: Boolean) : ManagePhase
    data object Connecting : ManagePhase
    data object Status : ManagePhase
    data object Choosing : ManagePhase
    data class Working(val message: String) : ManagePhase
    data class Joined(val ssid: String, val online: Boolean, val message: String?) : ManagePhase

    /** [ssid]: the network the Pi is on after falling back, null when none. */
    data class JoinFailed(val message: String, val ssid: String?) : ManagePhase
    data object Expired : ManagePhase
    data object Outdated : ManagePhase
    data object Unsupported : ManagePhase
    data class Failed(val message: String) : ManagePhase

    val isTerminal: Boolean get() = this is Expired || this is Outdated || this is Unsupported || this is Failed

    val isFinding: Boolean get() = this is Searching || this is Connecting || this is Bluetooth
}

/**
 * Managing one screen with its sticker: finds the Pi among the candidates, keeps a session and turns
 * responses into what the UI shows (docs/ble-management.md). Main thread only; a port of
 * `ManageClient.swift`.
 */
class ManageClient(
    val link: ManageLink,
    private val transport: ManageTransport,
    private val scope: CoroutineScope,
    private val timing: Timing = Timing(),
) {
    /** The job timeout stays above the Pi's own limit (JOB_MAX 150 s), so its final wins. */
    data class Timing(
        val responseRead: Duration = 1500.milliseconds,
        val responseWait: Duration = 8.seconds,
        val poll: Duration = 10.seconds,
        val jobTimeout: Duration = 180.seconds,
        val scanTimeout: Duration = 60.seconds,
        val retryDelay: Duration = 1.seconds,
    ) {
        fun scaled(speed: Double) = Timing(
            responseRead / speed, responseWait / speed, poll / speed, jobTimeout / speed, scanTimeout / speed,
            retryDelay / speed,
        )
    }

    private val _phase = MutableStateFlow<ManagePhase>(ManagePhase.Searching(slow = false))
    private val _status = MutableStateFlow<ManageStatus?>(null)
    private val _networks = MutableStateFlow<List<WifiNetwork>>(emptyList())
    private val _isScanning = MutableStateFlow(false)
    private val _isRefreshing = MutableStateFlow(false)
    private val _isReconnecting = MutableStateFlow(false)
    private val _isConnected = MutableStateFlow(false)
    private val _notice = MutableStateFlow<String?>(null)

    val phase: StateFlow<ManagePhase> = _phase.asStateFlow()
    val status: StateFlow<ManageStatus?> = _status.asStateFlow()
    val networks: StateFlow<List<WifiNetwork>> = _networks.asStateFlow()
    val isScanning: StateFlow<Boolean> = _isScanning.asStateFlow()
    val isRefreshing: StateFlow<Boolean> = _isRefreshing.asStateFlow()
    val isReconnecting: StateFlow<Boolean> = _isReconnecting.asStateFlow()
    val isConnected: StateFlow<Boolean> = _isConnected.asStateFlow()
    val notice: StateFlow<String?> = _notice.asStateFlow()

    val usesBluetooth: Boolean get() = transport.usesBluetooth

    private class Session(val key: ByteArray, val generation: Int) {
        var ctr = 0L
        var lastResponse = 0L
    }

    private data class Pending(val kind: Kind, val re: Long, val generation: Int, val accepted: Boolean = false) {
        sealed interface Kind {
            data object Scan : Kind
            data class Connect(val ssid: String) : Kind
        }
    }

    private class Stop(val phase: ManagePhase) : Exception()

    private var session: Session? = null
        set(value) {
            field = value
            _isConnected.value = value != null
        }
    private var work = CoroutineScope(scope.coroutineContext + SupervisorJob(scope.coroutineContext.job))
    private var generation = 0
    private var inFlight = false
    private var stopped = false
    private var runner: Job? = null
    private var pending: Pending? = null
    private var jobTimer: Job? = null
    private var poller: Job? = null
    private val inbox = mutableMapOf<Long, ManageResponse>()
    private val waiters = mutableMapOf<Long, CompletableDeferred<ManageResponse?>>()

    private val establishing: Boolean get() = runner?.isActive == true

    init {
        transport.onEvent = ::handle
    }

    fun start() {
        stopped = false
        work = CoroutineScope(scope.coroutineContext + SupervisorJob(scope.coroutineContext.job))
        runner = work.launch { establish() }
    }

    fun stop() {
        stopped = true
        work.cancel()
        clearJob()
        failWaiters()
        session = null
        transport.stop()
    }

    /** Starts over after a failure. */
    fun retry() {
        stop()
        _phase.value = ManagePhase.Searching(slow = false)
        _status.value = null
        _networks.value = emptyList()
        _notice.value = null
        _isReconnecting.value = false
        start()
    }

    /** Looks at Bluetooth again after the user changed a permission or setting. */
    fun recheckBluetooth() = transport.recheck()

    suspend fun refreshStatus() {
        if (session == null || inFlight || _isRefreshing.value) return
        _isRefreshing.value = true
        try {
            run(ManageRequest.Status)?.let { awaitResponse(it) }
        } finally {
            _isRefreshing.value = false
        }
    }

    fun refresh() {
        work.launch { refreshStatus() }
    }

    fun chooseNetwork() {
        _notice.value = null
        _phase.value = ManagePhase.Choosing
        rescan()
    }

    fun rescan() {
        if (session == null || inFlight || pending != null) return
        _notice.value = null
        startJob(Pending.Kind.Scan, ManageRequest.Scan, timing.scanTimeout)
        _isScanning.value = true
    }

    fun connect(ssid: String, password: String, hidden: Boolean) {
        if (session == null || inFlight) return
        if (pending?.kind == Pending.Kind.Scan) clearJob()
        if (pending != null) return
        _notice.value = null
        _phase.value = ManagePhase.Working("Verbinden met $ssid…")
        startJob(Pending.Kind.Connect(ssid), ManageRequest.Connect(ssid, password, hidden), timing.jobTimeout)
    }

    fun backToStatus() {
        _phase.value = ManagePhase.Status
        refresh()
    }

    fun backToList() {
        _phase.value = ManagePhase.Choosing
    }

    private suspend fun establish() {
        while (!stopped) {
            val candidates = try {
                transport.candidates(link.localName)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                return
            }
            if (_phase.value.isFinding) _phase.value = ManagePhase.Connecting
            var rejected = 0
            var unsupported = 0
            for (candidate in candidates) {
                try {
                    transport.connect(candidate)
                    handshake()
                    val re = send(ManageRequest.Status)
                    awaitResponse(re) ?: throw ManageLinkException.Timeout()
                    _isReconnecting.value = false
                    if (_phase.value.isFinding) _phase.value = ManagePhase.Status
                    return
                } catch (e: CancellationException) {
                    throw e
                } catch (e: ManageLinkException.Rejected) {
                    rejected += 1
                } catch (e: Stop) {
                    return abandon(e.phase)
                } catch (e: ManageLinkException.Malformed) {
                    return abandon(ManagePhase.Failed(BUG_MESSAGE))
                } catch (e: ManageLinkException.Failed) {
                    return abandon(ManagePhase.Failed(BUG_MESSAGE))
                } catch (e: ManageLinkException.NotManagement) {
                    unsupported += 1
                } catch (e: Exception) {
                    if (stopped) return
                }
                session = null
                transport.disconnect()
            }
            if (candidates.isNotEmpty() && rejected == candidates.size) return end(ManagePhase.Expired)
            if (candidates.isNotEmpty() && unsupported == candidates.size) return end(ManagePhase.Unsupported)
            delay(timing.retryDelay)
        }
    }

    private suspend fun handshake() {
        session = null
        val na = ManageCrypto.randomNonce()
        transport.writeSession(na)
        val value = transport.readSession()
        val first = value.firstOrNull() ?: throw ManageLinkException.Malformed()
        if (first != ManageProtocol.VERSION) throw Stop(ManagePhase.Outdated)
        if (value.size != 1 + ManageProtocol.NONCE_LENGTH) throw ManageLinkException.Malformed()
        generation += 1
        inbox.clear()
        failWaiters()
        session = Session(ManageCrypto.sessionKey(link.secret, na, value.copyOfRange(1, value.size)), generation)
    }

    private fun linkLost() {
        if (establishing || stopped || _phase.value.isTerminal) return
        session = null
        failWaiters()
        _isReconnecting.value = true
        runner = work.launch { establish() }
    }

    private fun abandon(final: ManagePhase) {
        session = null
        transport.disconnect()
        end(final)
    }

    private fun end(final: ManagePhase) {
        clearJob()
        failWaiters()
        _isReconnecting.value = false
        _phase.value = final
    }

    /** [register] sees the request's `ctr` before the write: the Pi notifies its answer before the write response arrives. */
    private suspend fun send(request: ManageRequest, register: ((Long, Int) -> Unit)? = null): Long {
        val s = session ?: throw ManageLinkException.Disconnected()
        s.ctr += 1
        val ctr = s.ctr
        register?.invoke(ctr, s.generation)
        transport.writeRequest(ManageCrypto.seal(request.json(), s.key, Direction.REQUEST, ctr))
        return ctr
    }

    /** 0x08 inside a session (idle timeout, the Pi restarted): one new handshake, then one retry. */
    private suspend fun perform(request: ManageRequest, register: ((Long, Int) -> Unit)?): Long {
        inFlight = true
        try {
            return try {
                send(request, register)
            } catch (e: ManageLinkException.Rejected) {
                handshake()
                try {
                    send(request, register)
                } catch (e: ManageLinkException.Rejected) {
                    throw Stop(ManagePhase.Expired)
                }
            }
        } finally {
            inFlight = false
        }
    }

    private suspend fun run(request: ManageRequest, register: ((Long, Int) -> Unit)? = null): Long? {
        try {
            return perform(request, register)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Stop) {
            abandon(e.phase)
        } catch (e: ManageLinkException.Rejected) {
            abandon(ManagePhase.Expired)
        } catch (e: ManageLinkException.Malformed) {
            abandon(ManagePhase.Failed(BUG_MESSAGE))
        } catch (e: ManageLinkException.Failed) {
            abandon(ManagePhase.Failed(BUG_MESSAGE))
        } catch (e: Exception) {
            linkLost()
        }
        return null
    }

    private fun startJob(kind: Pending.Kind, request: ManageRequest, timeout: Duration) {
        clearJob()
        jobTimer = work.launch {
            delay(timeout)
            jobTimedOut()
        }
        poller = work.launch {
            while (true) {
                delay(timing.poll)
                if (pending == null) return@launch
                refreshStatus()
            }
        }
        work.launch {
            val re = run(request) { re, generation -> pending = Pending(kind, re, generation) }
            val current = pending
            if (re != null && current?.re == re) {
                pending = current.copy(accepted = true)
            } else if (re == null && current == null && !_phase.value.isTerminal) {
                clearJob()
                if (kind is Pending.Kind.Connect) {
                    _phase.value = ManagePhase.JoinFailed(DROPPED_MESSAGE, _status.value?.ssid)
                }
            }
        }
    }

    private fun clearJob() {
        pending = null
        _isScanning.value = false
        jobTimer?.cancel()
        poller?.cancel()
    }

    private fun jobTimedOut() {
        val p = pending ?: return
        clearJob()
        if (p.kind is Pending.Kind.Connect) {
            _phase.value = ManagePhase.JoinFailed(
                "Het scherm reageert niet meer. Kijk op het scherm of het online is.",
                _status.value?.ssid,
            )
        }
    }

    internal fun handle(event: ManageTransportEvent) {
        val phase = _phase.value
        when (event) {
            is ManageTransportEvent.Bluetooth -> if (phase.isFinding) _phase.value = ManagePhase.Bluetooth(event.problem)
            ManageTransportEvent.Searching -> if (phase.isFinding) _phase.value = ManagePhase.Searching(slow = false)
            ManageTransportEvent.NotFoundYet -> if (phase is ManagePhase.Searching) _phase.value = ManagePhase.Searching(slow = true)
            is ManageTransportEvent.Response -> handleFrame(event.frame, event.read)
            ManageTransportEvent.Disconnected -> linkLost()
        }
    }

    private fun handleFrame(frame: ByteArray, read: Boolean) {
        val s = session ?: return
        val opened = try {
            ManageCrypto.open(frame, s.key, Direction.RESPONSE)
        } catch (e: FrameException) {
            // Another phone's frame, or one cut off at the MTU: the read returns ours in full.
            if (!read) transport.readResponse()
            return
        }
        if (opened.ctr <= s.lastResponse) return
        val response = ManageResponse.decode(opened.plaintext) ?: return
        s.lastResponse = opened.ctr
        apply(response, s.generation)
    }

    private fun apply(r: ManageResponse, generation: Int) {
        inbox[r.re] = r
        when (r.op) {
            "status" -> if (r.ok == true) {
                val new = ManageStatus.from(r)
                _status.value = new
                conclude(new, r.re, generation)
            }
            "scan" -> {
                val p = pending
                if (p != null && p.kind == Pending.Kind.Scan && p.re == r.re && p.generation == generation && r.final) {
                    clearJob()
                    if (r.ok == true) {
                        _networks.value = r.networks ?: emptyList()
                    } else {
                        _notice.value = r.msg ?: "Netwerken zoeken lukte niet. Probeer het opnieuw."
                    }
                }
            }
            "connect" -> {
                val p = pending
                val kind = p?.kind
                if (p != null && kind is Pending.Kind.Connect && p.re == r.re && p.generation == generation) {
                    val target = kind.ssid
                    when {
                        !r.final -> if (_phase.value is ManagePhase.Working && r.msg != null) {
                            _phase.value = ManagePhase.Working(r.msg)
                        }
                        r.ok == true -> finishConnect(ManagePhase.Joined(r.ssid ?: target, r.online ?: false, r.msg))
                        r.error == "failed" ->
                            finishConnect(ManagePhase.JoinFailed(r.msg ?: "Verbinden met $target mislukt.", r.ssid))
                        else -> finishConnect(
                            ManagePhase.JoinFailed(
                                r.msg ?: "Het scherm kon deze opdracht niet uitvoeren.",
                                _status.value?.ssid,
                            ),
                        )
                    }
                }
            }
        }
        waiters.remove(r.re)?.complete(r)
    }

    /** A final that went to an earlier session (the link dropped mid-job) is in `status.job`. */
    private fun conclude(status: ManageStatus, re: Long, generation: Int) {
        val p = pending ?: return
        if (generation <= p.generation && re <= p.re) return
        val job = status.job
        when (val kind = p.kind) {
            is Pending.Kind.Connect -> {
                val target = kind.ssid
                val ours = job?.op == "connect" && job?.ssid == target
                if (!p.accepted) {
                    if (generation <= p.generation) return
                    if (ours && job?.state == "busy") {
                        pending = p.copy(accepted = true)
                    } else {
                        finishConnect(ManagePhase.JoinFailed(DROPPED_MESSAGE, status.ssid))
                    }
                    return
                }
                if (!ours || job == null || job.state == "busy") return
                if (job.state == "ok") {
                    finishConnect(ManagePhase.Joined(target, status.online ?: false, job.msg))
                } else {
                    finishConnect(ManagePhase.JoinFailed(job.msg ?: "Verbinden met $target mislukt.", status.ssid))
                }
            }
            Pending.Kind.Scan ->
                if (generation > p.generation || (job?.op == "scan" && job?.state != "busy")) clearJob()
        }
    }

    private fun finishConnect(outcome: ManagePhase) {
        clearJob()
        _phase.value = outcome
    }

    private suspend fun awaitResponse(re: Long): ManageResponse? {
        inbox[re]?.let { return it }
        val generation = generation
        val waiter = CompletableDeferred<ManageResponse?>()
        waiters.remove(re)?.complete(null)
        waiters[re] = waiter
        val reader = work.launch {
            delay(timing.responseRead)
            if (this@ManageClient.generation == generation && inbox[re] == null) transport.readResponse()
        }
        try {
            return withTimeoutOrNull(timing.responseWait) { waiter.await() }
        } finally {
            reader.cancel()
            if (waiters[re] === waiter) waiters.remove(re)
        }
    }

    private fun failWaiters() {
        val all = waiters.values.toList()
        waiters.clear()
        all.forEach { it.complete(null) }
    }

    companion object {
        const val EXPIRED_MESSAGE =
            "Deze sticker werkt niet meer: er is een nieuwe QR gemaakt of het scherm is opnieuw gekoppeld. Print de nieuwe QR uit Narrowcasting en plak die op het scherm."
        const val DROPPED_MESSAGE = "De verbinding met het scherm viel weg. Probeer het opnieuw."
        const val BUG_MESSAGE = "Het scherm begreep de app niet. Probeer het opnieuw."
    }
}
