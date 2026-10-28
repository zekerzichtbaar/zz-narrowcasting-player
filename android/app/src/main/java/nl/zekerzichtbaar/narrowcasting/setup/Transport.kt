package nl.zekerzichtbaar.narrowcasting.setup

enum class BluetoothProblem { OFF, UNAUTHORIZED, UNSUPPORTED, LOCATION_OFF }

sealed interface TransportEvent {
    data class Bluetooth(val problem: BluetoothProblem) : TransportEvent
    data object Searching : TransportEvent
    data object NotFoundYet : TransportEvent
    data object Connecting : TransportEvent
    data object Connected : TransportEvent
    data class Status(val status: SetupStatus) : TransportEvent
    data class Networks(val networks: List<WifiNetwork>) : TransportEvent
    data object WriteExpired : TransportEvent
    data class WriteFailed(val message: String) : TransportEvent
    data object Disconnected : TransportEvent
}

/**
 * The link to one Pi. Events arrive on the main thread; the BLE and mock transports implement it.
 * [start] may be called again for the same link to re-check Bluetooth after the user changed a setting.
 */
interface SetupTransport {
    var onEvent: ((TransportEvent) -> Unit)?
    var autoReconnect: Boolean
    val usesBluetooth: Boolean
    fun start(link: SetupLink)
    fun send(frame: ByteArray)
    fun stop()
}
