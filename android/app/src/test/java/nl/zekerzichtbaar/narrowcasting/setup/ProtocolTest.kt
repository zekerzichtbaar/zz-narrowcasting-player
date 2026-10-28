package nl.zekerzichtbaar.narrowcasting.setup

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

internal const val VECTOR_URL = "https://app.zekerzichtbaar.tv/setup?d=1E2F&k=AAECAwQFBgcICQoLDA0ODw"
internal val vectorToken = ByteArray(16) { it.toByte() }

private const val PYTHON_FRAME =
    "006a6465666768696a6b6c6d6e6f45150c51ba54031f54ea9ced86fde6690e21b3f420f07303c607ed9ecee16947936ae385d447dd15ad432c" +
        "865fa840881365455532f7d1a25981ebd34d90150f39ccd63cb146f6738fefb08608a8f1a59fdc3a8d1d53306f5d04177cdb04"

internal fun ByteArray.hex(): String = joinToString("") { "%02x".format(it) }

internal fun bytes(hex: String): ByteArray = hex.chunked(2).map { it.toInt(16).toByte() }.toByteArray()

class LinkTest {
    @Test fun parsesTheVectorUrl() {
        val link = required(SetupLink.parse(VECTOR_URL))
        assertEquals("1E2F", link.deviceId)
        assertTrue(link.token.contentEquals(vectorToken))
        assertEquals("ZZ-1E2F", link.localName)
    }

    @Test fun normalizesTheDeviceIdToUpperCase() {
        val link = required(SetupLink.parse(" https://x.test/setup?k=AAECAwQFBgcICQoLDA0ODw&d=1e2f \n"))
        assertEquals("1E2F", link.deviceId)
    }

    @Test fun recognizesTheDemoLink() {
        assertTrue(required(SetupLink.parse("https://app.zekerzichtbaar.tv/setup?d=demo&k=AAECAwQFBgcICQoLDA0ODw")).isDemo)
        assertFalse(required(SetupLink.parse(VECTOR_URL)).isDemo)
    }

    @Test fun rejectsBrokenLinks() {
        listOf(
            "https://app.zekerzichtbaar.tv/setup",
            "https://app.zekerzichtbaar.tv/setup?d=1E2F",
            "https://app.zekerzichtbaar.tv/setup?d=1E2&k=AAECAwQFBgcICQoLDA0ODw",
            "https://app.zekerzichtbaar.tv/setup?d=1E2F&k=AAECAwQFBgcICQoLDA0O",
            "https://app.zekerzichtbaar.tv/setup?d=1E-F&k=AAECAwQFBgcICQoLDA0ODw",
            "https://app.zekerzichtbaar.tv/setup?d=1E2F&k=!!!",
            "geen link",
        ).forEach { assertNull(it, SetupLink.parse(it)) }
    }

    @Test fun sameScreenAndTokenIsTheSameLink() {
        assertEquals(SetupLink.parse(VECTOR_URL), SetupLink.parse("$VECTOR_URL "))
    }
}

class CryptoTest {
    @Test fun derivesThePythonKey() {
        assertEquals("3e61773816500fece50152798adfe908941f25fa47fdac077f18bf7a8b6b6a44", FrameCrypto.key(vectorToken).hex())
    }

    @Test fun opensThePythonFrame() {
        val plain = FrameCrypto.open(bytes(PYTHON_FRAME), vectorToken)
        assertEquals(Command.Connect("Kantine", "geheim123", hidden = false), Command.fromJson(plain))
    }

    @Test fun sealsFramesThePiCanOpen() {
        val frame = FrameCrypto.seal(Command.Connect("Kantine", "geheim123", false), vectorToken, bytes("6465666768696a6b6c6d6e6f"))
        assertEquals(frame.size - 2, ((frame[0].toInt() and 0xff) shl 8) or (frame[1].toInt() and 0xff))
        assertTrue(frame.size <= SetupProtocol.MAX_VALUE_LENGTH)
        assertEquals(Command.Connect("Kantine", "geheim123", false), Command.fromJson(FrameCrypto.open(frame, vectorToken)))
        System.getenv("KOTLIN_FRAME_OUT")?.let { java.io.File(it).writeText(frame.hex()) }
    }

    @Test fun encodesCommandsLikeTheSwiftApp() {
        assertEquals("""{"op":"scan"}""", Command.Scan.json().decodeToString())
        assertEquals(
            """{"op":"connect","ssid":"Kantine","password":"geheim123","hidden":false}""",
            Command.Connect("Kantine", "geheim123", false).json().decodeToString(),
        )
    }

    @Test fun largestCommandFitsInOneValue() {
        val frame = FrameCrypto.seal(Command.Connect("\"".repeat(32), "\\".repeat(63), hidden = true), vectorToken)
        assertTrue(frame.size <= SetupProtocol.MAX_VALUE_LENGTH)
    }

    @Test fun wrongTokenIsUnauthorized() {
        val frame = FrameCrypto.seal(Command.Scan, ByteArray(16) { 7 })
        val wrong = assertThrows(FrameException::class.java) { FrameCrypto.open(frame, vectorToken) }
        assertEquals(FrameException.Reason.UNAUTHORIZED, wrong.reason)
        val short = assertThrows(FrameException::class.java) { FrameCrypto.open(frame.copyOf(frame.size - 1), vectorToken) }
        assertEquals(FrameException.Reason.BAD_LENGTH, short.reason)
    }
}

class PayloadTest {
    @Test fun decodesStatus() {
        val json = """{"v":1,"seq":7,"state":"online","msg":null,"ssid":"Kantine","scan":3,"code":"K7Q-4M2","url":"https://cms/schermen?code=K7Q-4M2"}"""
        val status = required(SetupStatus.decode(json.toByteArray()))
        assertEquals(SetupStatus.State.ONLINE, status.state)
        assertEquals(7, status.seq)
        assertEquals(3, status.scan)
        assertEquals("K7Q-4M2", status.code)
        assertEquals("https://cms/schermen?code=K7Q-4M2", status.url)
        assertEquals("Kantine", status.ssid)
        assertNull(status.msg)
    }

    @Test fun toleratesUnknownStatesAndTruncation() {
        assertEquals(SetupStatus.State.UNKNOWN, SetupStatus.decode("""{"v":2,"seq":1,"state":"rebooting","scan":0}""".toByteArray())?.state)
        assertEquals(SetupStatus.State.UNKNOWN, SetupStatus.decode("""{"seq":1,"state":"READY"}""".toByteArray())?.state)
        assertNull(SetupStatus.decode("""{"v":1,"seq":7,"state":"fail""".toByteArray()))
        assertEquals(0, SetupStatus.decode("""{"seq":1,"state":"ready","scan":null,"extra":true}""".toByteArray())?.scan)
    }

    @Test fun decodesNetworks() {
        val list = WifiNetwork.decodeList("""[["Kantine",82,1],["Gast",20,0],["kapot"]]""".toByteArray())
        assertEquals(listOf(WifiNetwork("Kantine", 82, true), WifiNetwork("Gast", 20, false)), list)
        assertEquals(emptyList<WifiNetwork>(), WifiNetwork.decodeList("[]".toByteArray()))
        assertNull(WifiNetwork.decodeList("[[\"Kantine\",82".toByteArray()))
    }

    @Test fun validatesLikeThePi() {
        assertNull(Validation.password("", secured = false))
        assertNotNull(Validation.password("", secured = true))
        assertNotNull(Validation.password("kort", secured = true))
        assertNull(Validation.password("geheim123", secured = true))
        assertNotNull(Validation.password("x".repeat(64), secured = true))
        assertNotNull(Validation.ssid("é".repeat(17)))
        assertNotNull(Validation.ssid("  "))
    }
}

internal fun <T : Any> required(value: T?): T {
    assertNotNull(value)
    return value!!
}
