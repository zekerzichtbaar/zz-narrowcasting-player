package nl.zekerzichtbaar.narrowcasting.setup

import java.util.UUID
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray

/**
 * A simulated Pi's management service with the real crypto and the rules of `pi_player/blemanage.py`:
 * 0x08 without a secret, a session or a fresh `ctr`; the final of a job only reaches the session that
 * started it. Password "fout1234" fails and falls back.
 */
class MockManagePi(
    secret: String?,
    var rssi: Int = -60,
    var device: String = "pi-10000000a1b2c3d4",
    var name: String? = "Kantine links",
) {
    val id: String = UUID.randomUUID().toString()
    var secret: String? = secret
        set(value) {
            if (value != field) session = null
            field = value
        }
    var version: Byte = ManageProtocol.VERSION
    var agentVersion = "v0.9.7"
    var screenState = "PLAYING"
    var paired = true
    var online: Boolean? = true
    var network: String? = "wifi"
    var ssid: String? = "Kantine"
    var signal: Int? = 82
    var ip: String? = "192.168.1.42"
    var ops = listOf("status", "scan", "connect")
    var networks = listOf(
        WifiNetwork("Kantine", 82, true),
        WifiNetwork("Zeker Zichtbaar", 74, true),
        WifiNetwork("Office 5G", 54, true),
        WifiNetwork("Gast", 31, false),
    )

    /** Ends the BLE link a moment into the next `connect`, as a wifi switch on the shared radio may. */
    var dropLinkDuringConnect = false
    var malformNextRequest = false
    var notifies = true

    /** An agent from before management: its GATT app has only the setup service. */
    var hasManagement = true
    val accepted = mutableListOf<ManageRequest>()
    var job: ManageJob? = null
        private set

    internal var notify: ((ByteArray) -> Unit)? = null
    internal var dropLink: (() -> Unit)? = null
    internal var scope: CoroutineScope? = null
    internal var speed = 1.0

    private class Session(val key: ByteArray, val np: ByteArray) {
        var lastRequest = 0L
        var responseCtr = 0L
        var latest = ByteArray(0)
    }

    private var session: Session? = null
    private var jobSession: Session? = null
    private val tasks = mutableListOf<Job>()

    internal val latestResponse: ByteArray? get() = session?.latest

    /** The connection went away or the session idled out (300 s on the Pi). */
    fun endSession() {
        session = null
    }

    internal fun stop() {
        tasks.forEach { it.cancel() }
        tasks.clear()
    }

    fun sessionWrite(na: ByteArray) {
        val secret = secret ?: throw ManageLinkException.Rejected()
        if (na.size != ManageProtocol.NONCE_LENGTH) throw ManageLinkException.Malformed()
        val np = ManageCrypto.randomNonce()
        session = Session(ManageCrypto.sessionKey(secret, na, np), np)
    }

    fun sessionRead(): ByteArray {
        val session = session ?: throw ManageLinkException.Rejected()
        return byteArrayOf(version) + session.np
    }

    fun requestWrite(frame: ByteArray) {
        if (secret == null) throw ManageLinkException.Rejected()
        if (malformNextRequest) {
            malformNextRequest = false
            throw ManageLinkException.Malformed()
        }
        val s = session ?: throw ManageLinkException.Rejected()
        val opened = try {
            ManageCrypto.open(frame, s.key, Direction.REQUEST)
        } catch (e: FrameException) {
            if (e.reason == FrameException.Reason.BAD_LENGTH) throw ManageLinkException.Malformed()
            throw ManageLinkException.Rejected()
        }
        if (opened.ctr <= s.lastRequest) throw ManageLinkException.Rejected()
        s.lastRequest = opened.ctr
        handle(s, opened.ctr, opened.plaintext)
    }

    private fun handle(s: Session, re: Long, plain: ByteArray) {
        val op = runCatching { SetupProtocol.json.parseToJsonElement(plain.decodeToString()).jsonObject["op"]?.jsonPrimitive }
            .getOrNull()?.takeIf { it.isString }?.content
            ?: return fail(s, re, null, "bad_request", "Ongeldig verzoek.")
        val request = ManageRequest.fromJson(plain)
        if (op !in ops || request == null) return fail(s, re, op, "unknown_op", null)
        accepted += request
        when (request) {
            ManageRequest.Status -> respond(s, buildJsonObject {
                put("re", re)
                put("op", op)
                put("ok", true)
                put("final", true)
                put("v", 1)
                putJsonArray("ops") { ops.forEach { add(JsonPrimitive(it)) } }
                put("device", device)
                put("name", name)
                put("agent_version", agentVersion)
                put("state", screenState)
                put("paired", paired)
                put("online", online)
                put("network", network)
                put("ssid", ssid)
                put("signal", signal)
                put("ip", ip)
                put("job", jobJson())
            })
            ManageRequest.Scan -> {
                if (job?.state == "busy") return busy(s, re, op)
                start(s, re, ManageJob(op, "busy"), "Netwerken zoeken…")
                later(2.0) {
                    finish(re, ManageJob(op, "ok"), buildJsonObject {
                        put("ok", true)
                        putJsonArray("networks") {
                            networks.forEach { n ->
                                add(buildJsonArray {
                                    add(JsonPrimitive(n.ssid))
                                    add(JsonPrimitive(n.signal))
                                    add(JsonPrimitive(if (n.secured) 1 else 0))
                                })
                            }
                        }
                    })
                }
            }
            is ManageRequest.Connect -> {
                if (job?.state == "busy") return busy(s, re, op)
                val target = request.ssid
                val problem = Validation.ssid(target) ?: Validation.password(request.password, secured = false)
                if (problem != null) return fail(s, re, op, "bad_request", problem)
                val old = ssid
                start(s, re, ManageJob(op, "busy", target), "Verbinden met $target…")
                if (dropLinkDuringConnect) {
                    dropLinkDuringConnect = false
                    later(1.0) {
                        session = null
                        dropLink?.invoke()
                    }
                }
                later(3.0) {
                    if (request.password == WRONG_PASSWORD) {
                        val msg = "Verbinden met $target mislukt: Verkeerd wachtwoord?" + (old?.let { " Terug naar $it." } ?: "")
                        finish(re, ManageJob(op, "failed", target, msg), buildJsonObject {
                            put("ok", false)
                            put("error", "failed")
                            put("msg", msg)
                            put("ssid", old)
                        })
                    } else {
                        ssid = target
                        signal = 74
                        ip = "192.168.1.57"
                        online = true
                        network = "wifi"
                        finish(re, ManageJob(op, "ok", target), buildJsonObject {
                            put("ok", true)
                            put("msg", JsonNull)
                            put("ssid", target)
                            put("ip", "192.168.1.57")
                            put("online", true)
                        })
                    }
                }
            }
        }
    }

    private fun jobJson() = job?.let {
        buildJsonObject {
            put("op", it.op)
            put("state", it.state)
            put("ssid", it.ssid)
            put("msg", it.msg)
        }
    } ?: JsonNull

    private fun start(s: Session, re: Long, new: ManageJob, msg: String) {
        job = new
        jobSession = s
        respond(s, buildJsonObject {
            put("re", re)
            put("op", new.op)
            put("final", false)
            put("state", "busy")
            put("msg", msg)
        })
    }

    private fun finish(re: Long, done: ManageJob, fields: JsonObject) {
        job = done
        val s = session ?: return
        if (s !== jobSession) return
        respond(s, JsonObject(fields + mapOf("re" to JsonPrimitive(re), "op" to JsonPrimitive(done.op), "final" to JsonPrimitive(true))))
    }

    private fun busy(s: Session, re: Long, op: String) =
        fail(s, re, op, "busy", "De speler is nog bezig met een vorige opdracht.")

    private fun fail(s: Session, re: Long, op: String?, error: String, msg: String?) {
        respond(s, buildJsonObject {
            put("re", re)
            put("op", op)
            put("ok", false)
            put("final", true)
            put("error", error)
            put("msg", msg)
        })
    }

    private fun respond(s: Session, obj: JsonObject) {
        s.responseCtr += 1
        val frame = ManageCrypto.seal(obj.toString().toByteArray(), s.key, Direction.RESPONSE, s.responseCtr)
        s.latest = frame
        if (notifies) notify?.invoke(frame)
    }

    private fun later(seconds: Double, action: MockManagePi.() -> Unit) {
        val scope = scope ?: return
        tasks += scope.launch {
            delay((seconds / speed * 1000).toLong())
            action()
        }
    }

    companion object {
        const val WRONG_PASSWORD = "fout1234"
    }
}

/** Simulates the BLE link to one or more Pis with the same `ZZ-<d>` (the emulator has no Bluetooth). */
class MockManageTransport(
    val pis: List<MockManagePi>,
    private val scope: CoroutineScope,
    private val speed: Double = 1.0,
) : ManageTransport {
    override var onEvent: ((ManageTransportEvent) -> Unit)? = null
    override val usesBluetooth = false
    val connects = mutableListOf<String>()
    val requestFrames = mutableListOf<ByteArray>()
    var responseReads = 0
        private set

    private var current: MockManagePi? = null

    init {
        pis.forEach {
            it.scope = scope
            it.speed = speed
        }
    }

    override suspend fun candidates(localName: String): List<ManageCandidate> {
        emit(ManageTransportEvent.Searching)
        pause(1.2)
        pause(2.0)
        return pis.sortedByDescending { it.rssi }.map { ManageCandidate(it.id, it.rssi) }
    }

    override suspend fun connect(candidate: ManageCandidate) {
        pause(0.4)
        val pi = pis.firstOrNull { it.id == candidate.id } ?: throw ManageLinkException.Disconnected()
        if (!pi.hasManagement) throw ManageLinkException.NotManagement()
        connects += pi.id
        current = pi
        pi.notify = { frame -> if (current === pi) emit(ManageTransportEvent.Response(frame, read = false)) }
        pi.dropLink = {
            if (current === pi) {
                current = null
                emit(ManageTransportEvent.Disconnected)
            }
        }
    }

    override suspend fun writeSession(value: ByteArray) {
        pause(0.05)
        connected().sessionWrite(value)
    }

    override suspend fun readSession(): ByteArray {
        pause(0.05)
        return connected().sessionRead()
    }

    override suspend fun writeRequest(frame: ByteArray) {
        pause(0.05)
        requestFrames += frame
        connected().requestWrite(frame)
    }

    override fun readResponse() {
        val pi = current ?: return
        responseReads += 1
        scope.launch {
            pause(0.05)
            if (current === pi) pi.latestResponse?.let { emit(ManageTransportEvent.Response(it, read = true)) }
        }
    }

    override fun disconnect() {
        current?.endSession()
        current = null
    }

    override fun stop() {
        disconnect()
        pis.forEach { it.stop() }
    }

    override fun recheck() = Unit

    private fun connected(): MockManagePi = current ?: throw ManageLinkException.Disconnected()

    private fun emit(event: ManageTransportEvent) {
        onEvent?.invoke(event)
    }

    private suspend fun pause(seconds: Double) = delay((seconds / speed * 1000).toLong())

    companion object {
        /** One screen that accepts the link's secret, for the emulator and the `DEMO` sticker. */
        fun demo(link: ManageLink, scope: CoroutineScope, speed: Double = 1.0) =
            MockManageTransport(listOf(MockManagePi(link.secret)), scope, speed)
    }
}
