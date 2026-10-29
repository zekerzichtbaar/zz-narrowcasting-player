package nl.zekerzichtbaar.narrowcasting.setup

import java.security.SecureRandom
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull

/** Management over BLE with the sticker secret (docs/ble-management.md). */
object ManageProtocol {
    const val VERSION: Byte = 1
    val serviceUuid: UUID = UUID.fromString("8edd2620-ddcd-4435-a57e-98a6b4dd342f")
    val sessionUuid: UUID = UUID.fromString("8edd2621-ddcd-4435-a57e-98a6b4dd342f")
    val requestUuid: UUID = UUID.fromString("8edd2622-ddcd-4435-a57e-98a6b4dd342f")
    val responseUuid: UUID = UUID.fromString("8edd2623-ddcd-4435-a57e-98a6b4dd342f")
    const val NONCE_LENGTH = 16
    const val MAX_VALUE_LENGTH = 512

    internal val info = "zz-manage-v1".toByteArray()
    internal const val OVERHEAD = 2 + 4 + 16
}

/** What the sticker encodes: `<setup_url>?d=<id>&s=<secret>`. */
class ManageLink private constructor(val deviceId: String, internal val secret: String, val url: String) {
    val localName: String get() = "ZZ-$deviceId"
    val isDemo: Boolean get() = deviceId == "DEMO"

    override fun equals(other: Any?): Boolean =
        other is ManageLink && other.deviceId == deviceId && other.secret == secret

    override fun hashCode(): Int = 31 * deviceId.hashCode() + secret.hashCode()

    override fun toString(): String = "ManageLink($deviceId)"

    companion object {
        private val secretPattern = Regex("[A-Za-z0-9_-]{22,128}")

        fun parse(text: String): ManageLink? {
            val url = text.trim()
            val items = queryItems(url) ?: return null
            return from(items, url)
        }

        internal fun from(items: List<Pair<String, String?>>, url: String): ManageLink? {
            val d = deviceId(items) ?: return null
            val s = items.value("s")?.takeIf { secretPattern.matches(it) } ?: return null
            return ManageLink(d, s, url)
        }
    }
}

/** A `/setup` link: `k` is first-time setup and wins, `s` without `k` is management. */
sealed interface ScreenLink {
    val deviceId: String

    data class Setup(val link: SetupLink) : ScreenLink {
        override val deviceId: String get() = link.deviceId
    }

    data class Manage(val link: ManageLink) : ScreenLink {
        override val deviceId: String get() = link.deviceId
    }

    companion object {
        fun parse(text: String): ScreenLink? {
            val url = text.trim()
            val items = queryItems(url) ?: return null
            return if (items.any { it.first == "k" }) {
                SetupLink.parse(url)?.let(::Setup)
            } else {
                ManageLink.from(items, url)?.let(::Manage)
            }
        }
    }
}

enum class Direction(val byte: Byte) { REQUEST(1), RESPONSE(2) }

class OpenedFrame(val ctr: Long, val plaintext: ByteArray)

/**
 * Frames: `len (2) | ctr (4) | ciphertext + tag (16)`, AES-256-GCM under the session key,
 * nonce `dir | 7 zero bytes | ctr`, additional data `"zz-manage-v1" | dir`. Counters are unsigned 32 bit.
 */
object ManageCrypto {
    private const val TAG = 16
    private val random = SecureRandom()

    fun sessionKey(secret: String, na: ByteArray, np: ByteArray): ByteArray {
        val prk = hmac(SetupProtocol.salt, secret.toByteArray(Charsets.UTF_8))
        return hmac(prk, ManageProtocol.info + na + np + byteArrayOf(1))
    }

    fun seal(plaintext: ByteArray, key: ByteArray, direction: Direction, ctr: Long): ByteArray {
        val counter = be32(ctr)
        val body = counter + cipher(Cipher.ENCRYPT_MODE, key, direction, counter).doFinal(plaintext)
        return byteArrayOf((body.size shr 8).toByte(), (body.size and 0xff).toByte()) + body
    }

    /** Throws [FrameException] `BAD_LENGTH` for a malformed frame, `UNAUTHORIZED` when it is not this key's. */
    fun open(frame: ByteArray, key: ByteArray, direction: Direction): OpenedFrame {
        val length = ((frame.getOrElse(0) { 0 }.toInt() and 0xff) shl 8) or (frame.getOrElse(1) { 0 }.toInt() and 0xff)
        if (frame.size < ManageProtocol.OVERHEAD || length != frame.size - 2) {
            throw FrameException(FrameException.Reason.BAD_LENGTH)
        }
        return try {
            val plaintext = cipher(Cipher.DECRYPT_MODE, key, direction, frame.copyOfRange(2, 6)).doFinal(frame, 6, frame.size - 6)
            OpenedFrame(counter(frame), plaintext)
        } catch (e: java.security.GeneralSecurityException) {
            throw FrameException(FrameException.Reason.UNAUTHORIZED)
        }
    }

    fun randomNonce(): ByteArray = ByteArray(ManageProtocol.NONCE_LENGTH).also(random::nextBytes)

    internal fun counter(frame: ByteArray): Long =
        frame.copyOfRange(2, 6).fold(0L) { acc, b -> (acc shl 8) or (b.toLong() and 0xff) }

    private fun be32(value: Long): ByteArray =
        byteArrayOf((value shr 24).toByte(), (value shr 16).toByte(), (value shr 8).toByte(), value.toByte())

    private fun cipher(mode: Int, key: ByteArray, direction: Direction, counter: ByteArray): Cipher =
        Cipher.getInstance("AES/GCM/NoPadding").apply {
            val nonce = byteArrayOf(direction.byte) + ByteArray(7) + counter
            init(mode, SecretKeySpec(key, "AES"), GCMParameterSpec(TAG * 8, nonce))
            updateAAD(ManageProtocol.info + byteArrayOf(direction.byte))
        }

    private fun hmac(key: ByteArray, data: ByteArray): ByteArray =
        Mac.getInstance("HmacSHA256").run {
            init(SecretKeySpec(key, "HmacSHA256"))
            doFinal(data)
        }
}

sealed interface ManageRequest {
    val op: String

    data object Status : ManageRequest {
        override val op = "status"
    }

    data object Scan : ManageRequest {
        override val op = "scan"
    }

    data class Connect(val ssid: String, val password: String, val hidden: Boolean) : ManageRequest {
        override val op = "connect"

        override fun toString(): String = "Connect($ssid, hidden=$hidden)"
    }

    @Serializable
    private data class Wire(
        val op: String,
        val ssid: String? = null,
        val password: String? = null,
        val hidden: Boolean? = null,
    )

    fun json(): ByteArray {
        val wire = when (this) {
            is Connect -> Wire(op, ssid, password, hidden)
            else -> Wire(op)
        }
        return SetupProtocol.json.encodeToString(Wire.serializer(), wire).toByteArray()
    }

    companion object {
        fun fromJson(data: ByteArray): ManageRequest? {
            val wire = runCatching {
                SetupProtocol.json.decodeFromString(Wire.serializer(), data.decodeToString())
            }.getOrNull() ?: return null
            return when (wire.op) {
                "status" -> Status
                "scan" -> Scan
                "connect" -> Connect(wire.ssid ?: "", wire.password ?: "", wire.hidden ?: false)
                else -> null
            }
        }
    }
}

/** The last `scan`/`connect` a phone started, as `status.job` reports it. */
data class ManageJob(val op: String, val state: String, val ssid: String? = null, val msg: String? = null)

/** One decrypted response. `state` is the TV state in a `status` answer and `busy` in progress. */
data class ManageResponse(
    val re: Long,
    val op: String? = null,
    val ok: Boolean? = null,
    val final: Boolean = ok != null,
    val state: String? = null,
    val error: String? = null,
    val msg: String? = null,
    val ssid: String? = null,
    val ip: String? = null,
    val online: Boolean? = null,
    val networks: List<WifiNetwork>? = null,
    val v: Int? = null,
    val ops: List<String>? = null,
    val device: String? = null,
    val name: String? = null,
    val agentVersion: String? = null,
    val paired: Boolean? = null,
    val network: String? = null,
    val signal: Int? = null,
    val job: ManageJob? = null,
) {
    companion object {
        /** Lossy: a field with an unexpected type reads as missing; only `re` is required. */
        fun decode(data: ByteArray): ManageResponse? {
            val o = runCatching { SetupProtocol.json.parseToJsonElement(data.decodeToString()) as? JsonObject }
                .getOrNull() ?: return null
            val re = o.primitive("re")?.longOrNull ?: return null
            val ok = o.bool("ok")
            return ManageResponse(
                re = re,
                op = o.string("op"),
                ok = ok,
                final = o.bool("final") ?: (ok != null),
                state = o.string("state"),
                error = o.string("error"),
                msg = o.string("msg"),
                ssid = o.string("ssid"),
                ip = o.string("ip"),
                online = o.bool("online"),
                networks = (o["networks"] as? JsonArray)?.let(WifiNetwork::fromRows),
                v = o.int("v"),
                ops = (o["ops"] as? JsonArray)?.let { list ->
                    list.map { (it as? JsonPrimitive)?.takeIf { p -> p.isString }?.content ?: return@let null }
                },
                device = o.string("device"),
                name = o.string("name"),
                agentVersion = o.string("agent_version"),
                paired = o.bool("paired"),
                network = o.string("network"),
                signal = o.int("signal"),
                job = (o["job"] as? JsonObject)?.let { j ->
                    val op = j.string("op") ?: return@let null
                    val state = j.string("state") ?: return@let null
                    ManageJob(op, state, j.string("ssid"), j.string("msg"))
                },
            )
        }

        private fun JsonObject.primitive(key: String): JsonPrimitive? =
            (get(key) as? JsonPrimitive)?.takeIf { it !is JsonNull }

        private fun JsonObject.string(key: String): String? = primitive(key)?.takeIf { it.isString }?.content

        private fun JsonObject.bool(key: String): Boolean? = primitive(key)?.takeIf { !it.isString }?.booleanOrNull

        private fun JsonObject.int(key: String): Int? = primitive(key)?.takeIf { !it.isString }?.intOrNull
    }
}

/** What the status screen shows; every field may be missing on an older or offline Pi. */
data class ManageStatus(
    val device: String? = null,
    val name: String? = null,
    val agentVersion: String? = null,
    val screenState: String? = null,
    val paired: Boolean? = null,
    val online: Boolean? = null,
    val network: String? = null,
    val ssid: String? = null,
    val signal: Int? = null,
    val ip: String? = null,
    val ops: List<String> = emptyList(),
    val job: ManageJob? = null,
) {
    val canChangeWifi: Boolean get() = "scan" in ops && "connect" in ops

    companion object {
        fun from(r: ManageResponse) = ManageStatus(
            device = r.device,
            name = r.name,
            agentVersion = r.agentVersion,
            screenState = r.state,
            paired = r.paired,
            online = r.online,
            network = r.network,
            ssid = r.ssid,
            signal = r.signal,
            ip = r.ip,
            ops = r.ops ?: emptyList(),
            job = r.job,
        )
    }
}
