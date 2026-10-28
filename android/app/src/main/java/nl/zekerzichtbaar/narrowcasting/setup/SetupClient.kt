package nl.zekerzichtbaar.narrowcasting.setup

import kotlin.time.Duration
import kotlin.time.Duration.Companion.seconds
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

sealed interface Phase {
    data class Bluetooth(val problem: BluetoothProblem) : Phase
    data class Searching(val slow: Boolean) : Phase
    data object Connecting : Phase
    data object Choosing : Phase
    data class Working(val message: String) : Phase
    data class Failed(val message: String) : Phase
    data object Expired : Phase
    data class Online(val ssid: String?, val code: String?, val url: String?) : Phase
    data class Paired(val ssid: String?) : Phase

    val isFinished: Boolean get() = this is Online || this is Paired
}

/** The setup flow for one screen: turns transport events into what the UI shows. Main thread only. */
class SetupClient(
    val link: SetupLink,
    private val transport: SetupTransport,
    private val scope: CoroutineScope,
    private val commandTimeout: Duration = 75.seconds,
) {
    private val _phase = MutableStateFlow<Phase>(Phase.Searching(slow = false))
    private val _networks = MutableStateFlow<List<WifiNetwork>>(emptyList())
    private val _isScanning = MutableStateFlow(false)
    private val _notice = MutableStateFlow<String?>(null)

    val phase: StateFlow<Phase> = _phase.asStateFlow()
    val networks: StateFlow<List<WifiNetwork>> = _networks.asStateFlow()
    val isScanning: StateFlow<Boolean> = _isScanning.asStateFlow()

    /** A problem from before this session (e.g. a failed attempt via the hotspot), shown above the list. */
    val notice: StateFlow<String?> = _notice.asStateFlow()

    val usesBluetooth: Boolean get() = transport.usesBluetooth

    private var status: SetupStatus? = null
    private var pending: Pending? = null
    private var dismissedSeq: Int? = null
    private var timeout: Job? = null

    private sealed interface Pending {
        data class Connect(val sentAt: Int) : Pending
        data class Scan(val scanId: Int) : Pending
    }

    init {
        transport.onEvent = ::handle
    }

    fun start() = transport.start(link)

    fun stop() {
        timeout?.cancel()
        transport.stop()
    }

    fun connect(ssid: String, password: String, hidden: Boolean) {
        if (!send(Command.Connect(ssid, password, hidden))) return
        pending = Pending.Connect(sentAt = status?.seq ?: -1)
        _notice.value = null
        _phase.value = Phase.Working("Verbinden met $ssid…")
        armTimeout()
    }

    fun rescan() {
        if (_isScanning.value || !send(Command.Scan)) return
        pending = Pending.Scan(scanId = status?.scan ?: 0)
        _isScanning.value = true
        armTimeout()
    }

    /** Back to the network list after a failure. */
    fun backToList() {
        dismissedSeq = status?.seq
        _notice.value = null
        _phase.value = Phase.Choosing
    }

    private fun send(command: Command): Boolean =
        try {
            transport.send(FrameCrypto.seal(command, link.token))
            true
        } catch (e: java.security.GeneralSecurityException) {
            _phase.value = Phase.Failed("Versleutelen mislukt: ${e.message}")
            false
        }

    private fun armTimeout() {
        timeout?.cancel()
        timeout = scope.launch {
            delay(commandTimeout)
            commandTimedOut()
        }
    }

    private fun commandTimedOut() {
        when (pending) {
            is Pending.Connect -> {
                pending = null
                _phase.value = Phase.Failed("Het scherm reageert niet. Probeer het opnieuw.")
            }
            is Pending.Scan -> {
                pending = null
                _isScanning.value = false
            }
            null -> Unit
        }
    }

    internal fun handle(event: TransportEvent) {
        val phase = _phase.value
        when (event) {
            is TransportEvent.Bluetooth -> if (!phase.isFinished) _phase.value = Phase.Bluetooth(event.problem)
            TransportEvent.Searching ->
                if (!phase.isFinished && phase != Phase.Expired) _phase.value = Phase.Searching(slow = false)
            TransportEvent.NotFoundYet -> if (phase is Phase.Searching) _phase.value = Phase.Searching(slow = true)
            TransportEvent.Connecting -> {
                status = null
                if (!phase.isFinished) _phase.value = Phase.Connecting
            }
            TransportEvent.Connected -> Unit
            is TransportEvent.Networks -> _networks.value = event.networks
            is TransportEvent.Status -> apply(event.status)
            TransportEvent.WriteExpired -> {
                pending = null
                _isScanning.value = false
                _phase.value = Phase.Expired
            }
            is TransportEvent.WriteFailed -> {
                pending = null
                _isScanning.value = false
                _phase.value = Phase.Failed(event.message)
            }
            TransportEvent.Disconnected -> if (!phase.isFinished && phase != Phase.Expired) {
                pending = null
                _isScanning.value = false
            }
        }
    }

    private fun apply(new: SetupStatus) {
        status?.let { if (new.seq < it.seq) return }
        status = new
        when (new.state) {
            SetupStatus.State.ONLINE -> return finish(Phase.Online(new.ssid, new.code, new.url))
            SetupStatus.State.PAIRED -> return finish(Phase.Paired(new.ssid))
            else -> Unit
        }
        if (_phase.value == Phase.Expired) return

        when (val p = pending) {
            is Pending.Connect -> {
                when {
                    new.state == SetupStatus.State.BUSY -> _phase.value = Phase.Working(new.msg ?: "Bezig…")
                    new.state == SetupStatus.State.FAILED && new.seq > p.sentAt -> {
                        pending = null
                        timeout?.cancel()
                        _phase.value = Phase.Failed(new.msg ?: "Verbinden mislukt.")
                    }
                    // The Pi starts the job a moment after the write, and reads "ready" while it checks
                    // the CMS after joining: wait for online/failed (or the timeout).
                    else -> Unit
                }
                return
            }
            is Pending.Scan -> {
                if (new.state == SetupStatus.State.READY && new.scan != p.scanId) {
                    pending = null
                    _isScanning.value = false
                    timeout?.cancel()
                } else if (new.state == SetupStatus.State.FAILED) {
                    pending = null
                    _isScanning.value = false
                    timeout?.cancel()
                    _notice.value = new.msg
                }
                if (!isFirstLook) return
            }
            null -> Unit
        }

        when (new.state) {
            SetupStatus.State.BUSY -> _phase.value = Phase.Working(new.msg ?: "Bezig…")
            SetupStatus.State.FAILED -> {
                _notice.value = if (new.seq == dismissedSeq) null else new.msg
                _phase.value = Phase.Choosing
            }
            else -> _phase.value = Phase.Choosing
        }
    }

    private val isFirstLook: Boolean
        get() = when (_phase.value) {
            is Phase.Searching, Phase.Connecting, is Phase.Bluetooth -> true
            else -> false
        }

    private fun finish(done: Phase) {
        pending = null
        _isScanning.value = false
        timeout?.cancel()
        transport.autoReconnect = false
        _phase.value = done
    }
}
