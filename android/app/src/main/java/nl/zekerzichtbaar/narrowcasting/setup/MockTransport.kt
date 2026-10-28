package nl.zekerzichtbaar.narrowcasting.setup

import kotlin.random.Random
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Simulates a Pi for the emulator and the store review's demo link. Password "fout1234" fails,
 * anything else gets the screen online with pairing code K7Q-4M2.
 */
class MockTransport(private val scope: CoroutineScope, private val speed: Double = 1.0) : SetupTransport {
    override var onEvent: ((TransportEvent) -> Unit)? = null
    override var autoReconnect = true
    override val usesBluetooth = false

    private var link: SetupLink? = null
    private var seq = 0
    private var scanId = 1
    private var status = SetupStatus(seq = 0, state = SetupStatus.State.READY, scan = 1)
    private val jobs = mutableListOf<Job>()

    override fun start(link: SetupLink) {
        if (this.link != null) return
        this.link = link
        later(0.0) { emit(TransportEvent.Searching) }
        later(1.2) { emit(TransportEvent.Connecting) }
        later(1.8) {
            emit(TransportEvent.Connected)
            publish(SetupStatus.State.READY)
            emit(TransportEvent.Networks(baseNetworks))
        }
    }

    override fun send(frame: ByteArray) {
        val link = link
        val plain = link?.let { runCatching { FrameCrypto.open(frame, it.token) }.getOrNull() }
        if (plain == null) {
            emit(TransportEvent.WriteExpired)
            return
        }
        when (val command = Command.fromJson(plain)) {
            is Command.Connect -> {
                val ssid = command.ssid
                later(0.6) { publish(SetupStatus.State.BUSY, msg = "Verbinden met $ssid…") }
                later(3.0) {
                    if (command.password == WRONG_PASSWORD) {
                        publish(SetupStatus.State.FAILED, msg = "Verbinden met $ssid mislukt: Verkeerd wachtwoord?")
                    } else {
                        publish(SetupStatus.State.ONLINE, ssid = ssid)
                    }
                }
                later(5.0) {
                    if (status.state != SetupStatus.State.ONLINE) return@later
                    publish(SetupStatus.State.ONLINE, ssid = ssid, code = "K7Q-4M2", url = PAIRING_URL)
                }
            }
            Command.Scan -> {
                later(0.6) { publish(SetupStatus.State.BUSY, msg = "Netwerken zoeken…") }
                later(2.5) {
                    scanId += 1
                    publish(SetupStatus.State.READY)
                    emit(TransportEvent.Networks(baseNetworks.map {
                        it.copy(signal = maxOf(5, it.signal - Random.nextInt(0, 13)))
                    } + WifiNetwork("Buren", 12, true)))
                }
            }
            null -> emit(TransportEvent.WriteFailed("Het scherm begreep de opdracht niet."))
        }
    }

    override fun stop() {
        jobs.forEach { it.cancel() }
        jobs.clear()
    }

    private fun publish(
        state: SetupStatus.State,
        msg: String? = null,
        ssid: String? = null,
        code: String? = null,
        url: String? = null,
    ) {
        seq += 1
        status = SetupStatus(seq = seq, state = state, msg = msg, ssid = ssid, scan = scanId, code = code, url = url)
        emit(TransportEvent.Status(status))
    }

    private fun emit(event: TransportEvent) {
        onEvent?.invoke(event)
    }

    private fun later(seconds: Double, action: MockTransport.() -> Unit) {
        jobs += scope.launch {
            delay((seconds / speed * 1000).toLong())
            action()
        }
    }

    companion object {
        const val WRONG_PASSWORD = "fout1234"
        const val PAIRING_URL = "https://app.zekerzichtbaar.tv/schermen?code=K7Q-4M2"

        private val baseNetworks = listOf(
            WifiNetwork("Kantine", 82, true),
            WifiNetwork("Zeker Zichtbaar", 74, true),
            WifiNetwork("Office 5G", 54, true),
            WifiNetwork("Gast", 31, false),
        )
    }
}
