package nl.zekerzichtbaar.narrowcasting.setup

/** One Pi advertising the sticker's `ZZ-<d>`; `id` is its Bluetooth address. */
data class ManageCandidate(val id: String, val rssi: Int)

sealed class ManageLinkException(message: String) : Exception(message) {
    /** ATT 0x08 insufficient authorization. */
    class Rejected : ManageLinkException("insufficient authorization")

    /** ATT 0x0e unlikely error: the Pi found the frame malformed. */
    class Malformed : ManageLinkException("malformed")

    class Failed(detail: String) : ManageLinkException(detail)
    class Disconnected : ManageLinkException("disconnected")
    class NotManagement : ManageLinkException("no management service")
    class Timeout : ManageLinkException("timeout")
}

sealed interface ManageTransportEvent {
    data class Bluetooth(val problem: BluetoothProblem) : ManageTransportEvent
    data object Searching : ManageTransportEvent
    data object NotFoundYet : ManageTransportEvent

    /** A RESPONSE value; `read` when it answers [ManageTransport.readResponse], else a notification. */
    class Response(val frame: ByteArray, val read: Boolean) : ManageTransportEvent

    /** The connected Pi went away without [ManageTransport.disconnect]. */
    data object Disconnected : ManageTransportEvent
}

/**
 * The management characteristics of one Pi at a time. The client owns the candidate, handshake and
 * retry rules; a transport only moves bytes. Calls and events are on the main thread.
 */
interface ManageTransport {
    var onEvent: ((ManageTransportEvent) -> Unit)?
    val usesBluetooth: Boolean

    /** Waits for the first advertisement named [localName], keeps listening ~2 s, strongest first. */
    suspend fun candidates(localName: String): List<ManageCandidate>

    /** Connects, finds the management service and subscribes to RESPONSE. */
    suspend fun connect(candidate: ManageCandidate)
    suspend fun writeSession(value: ByteArray)
    suspend fun readSession(): ByteArray
    suspend fun writeRequest(frame: ByteArray)
    fun readResponse()
    fun disconnect()
    fun stop()

    /** Looks at Bluetooth again (permission granted, back from Settings); idempotent. */
    fun recheck()
}
