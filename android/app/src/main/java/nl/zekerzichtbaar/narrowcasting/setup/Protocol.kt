package nl.zekerzichtbaar.narrowcasting.setup

import java.net.URI
import java.net.URLDecoder
import java.security.SecureRandom
import java.util.Base64
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.intOrNull

object SetupProtocol {
    const val VERSION = 1
    val serviceUuid: UUID = UUID.fromString("8edd2610-ddcd-4435-a57e-98a6b4dd342f")
    val statusUuid: UUID = UUID.fromString("8edd2611-ddcd-4435-a57e-98a6b4dd342f")
    val networksUuid: UUID = UUID.fromString("8edd2612-ddcd-4435-a57e-98a6b4dd342f")
    val commandUuid: UUID = UUID.fromString("8edd2613-ddcd-4435-a57e-98a6b4dd342f")
    const val MAX_VALUE_LENGTH = 512

    internal val aad = "zz-wifi-setup-v1".toByteArray()
    internal val salt = "zz-narrowcasting".toByteArray()

    internal val json = Json {
        ignoreUnknownKeys = true
        explicitNulls = false
        coerceInputValues = true
    }
}

/** What the QR on the TV encodes: `<setup_url>?d=<id>&k=<token>`. */
class SetupLink private constructor(val deviceId: String, val token: ByteArray, val url: String) {
    val localName: String get() = "ZZ-$deviceId"

    /** Device IDs are hex, so "DEMO" never matches a real screen; store review uses it without a Pi. */
    val isDemo: Boolean get() = deviceId == "DEMO"

    override fun equals(other: Any?): Boolean =
        other is SetupLink && other.deviceId == deviceId && other.token.contentEquals(token)

    override fun hashCode(): Int = 31 * deviceId.hashCode() + token.contentHashCode()

    override fun toString(): String = "SetupLink($deviceId)"

    companion object {
        fun parse(text: String): SetupLink? {
            val url = text.trim()
            val items = queryItems(url) ?: return null
            val d = deviceId(items) ?: return null
            val k = items.value("k") ?: return null
            val token = base64UrlDecode(k)?.takeIf { it.size == 16 } ?: return null
            return SetupLink(d, token, url)
        }
    }
}

/** `name` to `value` per query item; an item without `=` has a null value. */
internal fun queryItems(url: String): List<Pair<String, String?>>? {
    val query = runCatching { URI(url).rawQuery }.getOrNull() ?: return null
    return query.split('&').mapNotNull { part ->
        runCatching {
            decodeQuery(part.substringBefore('=')) to (if ('=' in part) decodeQuery(part.substringAfter('=')) else null)
        }.getOrNull()
    }
}

internal fun List<Pair<String, String?>>.value(name: String): String? =
    firstOrNull { it.first == name && it.second != null }?.second

internal fun deviceId(items: List<Pair<String, String?>>): String? {
    val d = items.value("d")?.uppercase() ?: return null
    return d.takeIf { it.length == 4 && it.all { c -> c in 'A'..'Z' || c in '0'..'9' } }
}

private fun decodeQuery(s: String): String = URLDecoder.decode(s.replace("+", "%2B"), "UTF-8")

sealed interface Command {
    data class Connect(val ssid: String, val password: String, val hidden: Boolean) : Command
    data object Scan : Command

    @Serializable
    private data class Wire(
        val op: String,
        val ssid: String? = null,
        val password: String? = null,
        val hidden: Boolean? = null,
    )

    fun json(): ByteArray {
        val wire = when (this) {
            is Connect -> Wire("connect", ssid, password, hidden)
            Scan -> Wire("scan")
        }
        return SetupProtocol.json.encodeToString(Wire.serializer(), wire).toByteArray()
    }

    companion object {
        fun fromJson(data: ByteArray): Command? {
            val wire = runCatching {
                SetupProtocol.json.decodeFromString(Wire.serializer(), data.decodeToString())
            }.getOrNull() ?: return null
            return when (wire.op) {
                "connect" -> Connect(wire.ssid ?: "", wire.password ?: "", wire.hidden ?: false)
                "scan" -> Scan
                else -> null
            }
        }
    }
}

class FrameException(val reason: Reason) : Exception(reason.name) {
    enum class Reason { BAD_LENGTH, UNAUTHORIZED }
}

/** COMMAND frames: `len (2, big endian) | nonce (12) | ciphertext + tag (16)`, AES-256-GCM. */
object FrameCrypto {
    private const val NONCE = 12
    private const val TAG = 16
    private val random = SecureRandom()

    fun key(token: ByteArray): ByteArray {
        val prk = hmac(SetupProtocol.salt, token)
        return hmac(prk, SetupProtocol.aad + byteArrayOf(1))
    }

    fun seal(command: Command, token: ByteArray, nonce: ByteArray = ByteArray(NONCE).also(random::nextBytes)): ByteArray {
        val cipher = cipher(Cipher.ENCRYPT_MODE, token, nonce)
        val body = nonce + cipher.doFinal(command.json())
        return byteArrayOf((body.size shr 8).toByte(), (body.size and 0xff).toByte()) + body
    }

    fun open(frame: ByteArray, token: ByteArray): ByteArray {
        if (frame.size < 2 + NONCE + TAG || frame.lengthField() != frame.size - 2) {
            throw FrameException(FrameException.Reason.BAD_LENGTH)
        }
        return try {
            cipher(Cipher.DECRYPT_MODE, token, frame.copyOfRange(2, 2 + NONCE))
                .doFinal(frame, 2 + NONCE, frame.size - 2 - NONCE)
        } catch (e: java.security.GeneralSecurityException) {
            throw FrameException(FrameException.Reason.UNAUTHORIZED)
        }
    }

    private fun ByteArray.lengthField(): Int = ((this[0].toInt() and 0xff) shl 8) or (this[1].toInt() and 0xff)

    private fun cipher(mode: Int, token: ByteArray, nonce: ByteArray): Cipher =
        Cipher.getInstance("AES/GCM/NoPadding").apply {
            init(mode, SecretKeySpec(key(token), "AES"), GCMParameterSpec(TAG * 8, nonce))
            updateAAD(SetupProtocol.aad)
        }

    private fun hmac(key: ByteArray, data: ByteArray): ByteArray =
        Mac.getInstance("HmacSHA256").run {
            init(SecretKeySpec(key, "HmacSHA256"))
            doFinal(data)
        }
}

data class SetupStatus(
    val v: Int = SetupProtocol.VERSION,
    val seq: Int,
    val state: State,
    val msg: String? = null,
    val ssid: String? = null,
    val scan: Int = 0,
    val code: String? = null,
    val url: String? = null,
) {
    enum class State { READY, BUSY, FAILED, ONLINE, PAIRED, UNKNOWN }

    @Serializable
    private data class Wire(
        val v: Int = SetupProtocol.VERSION,
        val seq: Int,
        val state: String,
        val msg: String? = null,
        val ssid: String? = null,
        val scan: Int = 0,
        val code: String? = null,
        val url: String? = null,
    )

    companion object {
        fun decode(data: ByteArray): SetupStatus? {
            val w = runCatching {
                SetupProtocol.json.decodeFromString(Wire.serializer(), data.decodeToString())
            }.getOrNull() ?: return null
            val state = State.entries.firstOrNull { it != State.UNKNOWN && it.name.lowercase() == w.state } ?: State.UNKNOWN
            return SetupStatus(w.v, w.seq, state, w.msg, w.ssid, w.scan, w.code, w.url)
        }
    }
}

data class WifiNetwork(val ssid: String, val signal: Int, val secured: Boolean) {
    companion object {
        /** NETWORKS value: `[[ssid, signal, secured 0/1], ...]`. */
        fun decodeList(data: ByteArray): List<WifiNetwork>? {
            val rows = runCatching { SetupProtocol.json.parseToJsonElement(data.decodeToString()) as? JsonArray }
                .getOrNull() ?: return null
            return fromRows(rows)
        }

        internal fun fromRows(rows: JsonArray): List<WifiNetwork> =
            rows.mapNotNull { row ->
                val cells = row as? JsonArray ?: return@mapNotNull null
                if (cells.size < 3) return@mapNotNull null
                val ssid = (cells[0] as? JsonPrimitive)?.takeIf { it.isString }?.content ?: return@mapNotNull null
                val signal = (cells[1] as? JsonPrimitive)?.takeIf { !it.isString }?.intOrNull ?: return@mapNotNull null
                val secured = (cells[2] as? JsonPrimitive)?.takeIf { !it.isString }?.intOrNull ?: return@mapNotNull null
                WifiNetwork(ssid, signal, secured != 0)
            }
    }
}

object Validation {
    fun password(password: String, secured: Boolean): String? {
        if (password.isEmpty()) return if (secured) "Vul het wachtwoord van dit netwerk in." else null
        return if (password.codePointCount(0, password.length) in 8..63) null else "Een wifi-wachtwoord is 8 tot 63 tekens."
    }

    fun ssid(ssid: String): String? {
        val trimmed = ssid.trim(' ', '\t')
        if (trimmed.isEmpty()) return "Typ de naam van het netwerk."
        return if (trimmed.toByteArray().size > 32) "Netwerknaam is te lang." else null
    }
}

internal fun base64UrlDecode(text: String): ByteArray? {
    var s = text.replace('-', '+').replace('_', '/')
    s += "=".repeat((4 - s.length % 4) % 4)
    return runCatching { Base64.getDecoder().decode(s) }.getOrNull()
}
