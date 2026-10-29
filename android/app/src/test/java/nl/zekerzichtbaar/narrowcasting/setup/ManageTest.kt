package nl.zekerzichtbaar.narrowcasting.setup

import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.currentTime
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

private const val VECTOR_SECRET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"
private val vectorNa = ByteArray(16) { it.toByte() }
private val vectorNp = ByteArray(16) { (it + 16).toByte() }
private const val VECTOR_KS = "58cb3bf3146d29f20289f4397eefeb0b283e00efab27c34056d96b894cbfa048"
private const val VECTOR_REQUEST = "0023000000016d4aebd66f2afc6e5d9fc7246f979f09d6a1a87cbd131eea5cef3524343cb0"
private const val VECTOR_RESPONSE =
    "004d00000001e9c9fbace569fc061f9e768a3bcd4adf7f064c9c5bf019ac29e316286054658ff8bb44cef05cac73d8c22bf66f10d8b2f7b4" +
        "4103a78bb7bedd0efa4219f3963c9eff9905b6022eeed5"
private const val STICKER_URL = "https://app.zekerzichtbaar.tv/setup?d=1E2F&s=$VECTOR_SECRET"
private const val OTHER_SECRET = "ZYXWVUTSRQPONMLKJIHGFEDCBA9876543210zyxwvut"
private const val TOKEN = "AAECAwQFBgcICQoLDA0ODw"

private val vectorKey: ByteArray get() = ManageCrypto.sessionKey(VECTOR_SECRET, vectorNa, vectorNp)

class ScreenLinkTest {
    @Test fun stickerOpensManagement() {
        val link = (required(ScreenLink.parse(STICKER_URL)) as ScreenLink.Manage).link
        assertEquals("1E2F", link.deviceId)
        assertEquals(VECTOR_SECRET, link.secret)
        assertEquals("ZZ-1E2F", link.localName)
        assertFalse(link.isDemo)
        assertFalse(link.toString().contains(VECTOR_SECRET))
        assertFalse(ScreenLink.Manage(link).toString().contains(VECTOR_SECRET))
    }

    @Test fun tvQrOpensSetup() {
        val setup = (required(ScreenLink.parse("https://app.zekerzichtbaar.tv/setup?d=1e2f&k=$TOKEN")) as ScreenLink.Setup).link
        assertEquals("1E2F", setup.deviceId)
        assertTrue(setup.token.contentEquals(vectorToken))
    }

    @Test fun setupWinsWhenBothArePresent() {
        assertTrue(ScreenLink.parse("$STICKER_URL&k=$TOKEN") is ScreenLink.Setup)
        assertTrue(ScreenLink.parse("https://app.zekerzichtbaar.tv/setup?k=$TOKEN&s=$VECTOR_SECRET&d=1E2F") is ScreenLink.Setup)
    }

    @Test fun aBrokenTokenNeverFallsThroughToManagement() {
        assertNull(ScreenLink.parse("$STICKER_URL&k=kapot"))
        assertNull(ScreenLink.parse("$STICKER_URL&k="))
        assertNull(ScreenLink.parse("$STICKER_URL&k"))
    }

    @Test fun demoStickerIsRecognized() {
        val link = required(ManageLink.parse("https://app.zekerzichtbaar.tv/setup?d=demo&s=$VECTOR_SECRET"))
        assertTrue(link.isDemo)
        assertEquals("DEMO", link.deviceId)
    }

    @Test fun secretLengthLimitsMatchThePi() {
        assertTrue(ManageLink.parse("https://x.test/setup?d=1E2F&s=${"a".repeat(22)}") != null)
        assertTrue(ManageLink.parse("https://x.test/setup?d=1E2F&s=${"a".repeat(128)}") != null)
        assertNull(ManageLink.parse("https://x.test/setup?d=1E2F&s=${"a".repeat(21)}"))
        assertNull(ManageLink.parse("https://x.test/setup?d=1E2F&s=${"a".repeat(129)}"))
    }

    @Test fun theSecretIsKeptExactlyAsInTheUrl() {
        val secret = "-_" + "A".repeat(41)
        assertEquals(secret, required(ManageLink.parse(" https://x.test/setup?s=$secret&d=1e2f \n")).secret)
    }

    @Test fun rejectsBrokenStickers() {
        listOf(
            "https://app.zekerzichtbaar.tv/setup",
            "https://app.zekerzichtbaar.tv/setup?d=1E2F",
            "https://app.zekerzichtbaar.tv/setup?s=$VECTOR_SECRET",
            "https://app.zekerzichtbaar.tv/setup?d=1E2&s=$VECTOR_SECRET",
            "https://app.zekerzichtbaar.tv/setup?d=1E2FF&s=$VECTOR_SECRET",
            "https://app.zekerzichtbaar.tv/setup?d=1E-F&s=$VECTOR_SECRET",
            "https://app.zekerzichtbaar.tv/setup?d=1E2F&s=0123456789abcdefghij+lmnopqrstuvwxyz",
            "https://app.zekerzichtbaar.tv/setup?d=1E2F&s=0123456789abcdefghijklmnopqrstuvwxyzABCDEF%3D",
            "geen link",
        ).forEach { assertNull(it, ScreenLink.parse(it)) }
    }

    @Test fun sameStickerIsTheSameLink() {
        assertEquals(ManageLink.parse(STICKER_URL), ManageLink.parse("$STICKER_URL "))
        assertFalse(ManageLink.parse(STICKER_URL) == ManageLink.parse("https://x.test/setup?d=1E2F&s=$OTHER_SECRET"))
    }
}

class ManageCryptoTest {
    @Test fun derivesTheSpecSessionKey() {
        assertEquals(VECTOR_KS, vectorKey.hex())
    }

    @Test fun sealsTheSpecRequest() {
        assertEquals(VECTOR_REQUEST, ManageCrypto.seal("""{"op":"status"}""".toByteArray(), vectorKey, Direction.REQUEST, 1).hex())
        assertEquals(VECTOR_REQUEST, ManageCrypto.seal(ManageRequest.Status.json(), vectorKey, Direction.REQUEST, 1).hex())
    }

    @Test fun opensTheSpecResponse() {
        val opened = ManageCrypto.open(bytes(VECTOR_RESPONSE), vectorKey, Direction.RESPONSE)
        assertEquals(1L, opened.ctr)
        assertEquals("""{"re":1,"op":"scan","ok":true,"final":true,"networks":[]}""", opened.plaintext.decodeToString())
        val response = required(ManageResponse.decode(opened.plaintext))
        assertEquals(1L, response.re)
        assertEquals("scan", response.op)
        assertEquals(true, response.ok)
        assertTrue(response.final)
        assertEquals(emptyList<WifiNetwork>(), response.networks)
    }

    @Test fun directionsNeverShareANonce() {
        assertUnauthorized { ManageCrypto.open(bytes(VECTOR_RESPONSE), vectorKey, Direction.REQUEST) }
        assertUnauthorized { ManageCrypto.open(bytes(VECTOR_REQUEST), vectorKey, Direction.RESPONSE) }
    }

    @Test fun anotherSessionOrSecretCannotOpenIt() {
        val otherSession = ManageCrypto.sessionKey(VECTOR_SECRET, vectorNp, vectorNa)
        val otherSticker = ManageCrypto.sessionKey(OTHER_SECRET, vectorNa, vectorNp)
        for (key in listOf(otherSession, otherSticker)) {
            assertUnauthorized { ManageCrypto.open(bytes(VECTOR_REQUEST), key, Direction.REQUEST) }
        }
        val request = bytes(VECTOR_REQUEST)
        assertBadLength { ManageCrypto.open(request.copyOf(request.size - 1), vectorKey, Direction.REQUEST) }
        assertBadLength { ManageCrypto.open(byteArrayOf(0, 4, 0, 0, 0, 1), vectorKey, Direction.REQUEST) }
        assertBadLength { ManageCrypto.open(byteArrayOf(), vectorKey, Direction.REQUEST) }
    }

    @Test fun countersAreUnsigned32Bit() {
        val frame = ManageCrypto.seal(ManageRequest.Status.json(), vectorKey, Direction.REQUEST, 0xFFFFFFFFL)
        assertEquals(0xFFFFFFFFL, ManageCrypto.open(frame, vectorKey, Direction.REQUEST).ctr)
    }

    @Test fun largestConnectFitsInOneValue() {
        val request = ManageRequest.Connect("\"".repeat(32), "\\".repeat(63), hidden = true)
        val frame = ManageCrypto.seal(request.json(), vectorKey, Direction.REQUEST, 0xFFFFFFFFL)
        assertTrue(frame.size <= ManageProtocol.MAX_VALUE_LENGTH)
    }

    /** `KOTLIN_MANAGE_FRAME_OUT=<file>`: a frame for `blemanage.unseal` to open (Kotlin -> Python). */
    @Test fun sealsAConnectThePiCanOpen() {
        val request = ManageRequest.Connect("Kantine", "geheim123", hidden = false)
        val frame = ManageCrypto.seal(request.json(), vectorKey, Direction.REQUEST, 2)
        val opened = ManageCrypto.open(frame, vectorKey, Direction.REQUEST)
        assertEquals(2L, opened.ctr)
        assertEquals(request, ManageRequest.fromJson(opened.plaintext))
        assertEquals("""{"op":"connect","ssid":"Kantine","password":"geheim123","hidden":false}""", request.json().decodeToString())
        assertFalse(request.toString().contains("geheim123"))
        System.getenv("KOTLIN_MANAGE_FRAME_OUT")?.let { java.io.File(it).writeText(frame.hex()) }
    }

    private fun assertUnauthorized(block: () -> Unit) =
        assertEquals(FrameException.Reason.UNAUTHORIZED, assertThrows(FrameException::class.java, block).reason)

    private fun assertBadLength(block: () -> Unit) =
        assertEquals(FrameException.Reason.BAD_LENGTH, assertThrows(FrameException::class.java, block).reason)
}

class ManagePayloadTest {
    @Test fun decodesTheSpecStatus() {
        val json = """
            {"re": 1, "op": "status", "ok": true, "final": true, "v": 1, "ops": ["status", "scan", "connect"],
             "device": "pi-10000000a1b2c3d4", "name": "Kantine links", "agent_version": "v0.9.7",
             "state": "PLAYING", "paired": true, "online": true,
             "network": "wifi", "ssid": "Kantine", "signal": 82, "ip": "192.168.1.42", "later": {"x": 1},
             "job": {"op": "connect", "state": "failed", "ssid": "Gast", "msg": "Verbinden met Gast mislukt: verkeerd wachtwoord?"}}
        """.trimIndent()
        val status = ManageStatus.from(required(ManageResponse.decode(json.toByteArray())))
        assertEquals("Kantine links", status.name)
        assertEquals("pi-10000000a1b2c3d4", status.device)
        assertEquals("v0.9.7", status.agentVersion)
        assertEquals("PLAYING", status.screenState)
        assertEquals(true, status.paired)
        assertEquals(true, status.online)
        assertEquals("wifi", status.network)
        assertEquals("Kantine", status.ssid)
        assertEquals(82, status.signal)
        assertEquals("192.168.1.42", status.ip)
        assertEquals(ManageJob("connect", "failed", "Gast", "Verbinden met Gast mislukt: verkeerd wachtwoord?"), status.job)
        assertTrue(status.canChangeWifi)
    }

    @Test fun toleratesNullsAndMissingOps() {
        val json = """{"re":4,"op":"status","ok":true,"final":true,"ops":["status"],"name":null,"online":null,"ssid":null,"signal":null,"ip":null,"job":null}"""
        val status = ManageStatus.from(required(ManageResponse.decode(json.toByteArray())))
        assertNull(status.name)
        assertNull(status.online)
        assertNull(status.ssid)
        assertNull(status.job)
        assertFalse(status.canChangeWifi)
    }

    @Test fun aWrongTypedFieldIsMissingNotFatal() {
        val json = """{"re":5,"op":"status","ok":true,"signal":"sterk","online":"ja","ops":["status",3],"name":"Kantine links"}"""
        val r = required(ManageResponse.decode(json.toByteArray()))
        assertNull(r.signal)
        assertNull(r.online)
        assertNull(r.ops)
        assertEquals("Kantine links", r.name)
        assertTrue(r.final)
        assertNull(ManageResponse.decode("""{"op":"status"}""".toByteArray()))
        assertNull(ManageResponse.decode("""{"re":1,"op":"stat""".toByteArray()))
    }

    @Test fun decodesProgressAndNetworks() {
        val progress = required(ManageResponse.decode("""{"re":2,"op":"connect","final":false,"state":"busy","msg":"Verbinden met Gast…"}""".toByteArray()))
        assertFalse(progress.final)
        assertEquals("busy", progress.state)
        assertNull(progress.ok)
        val scan = required(ManageResponse.decode("""{"re":3,"op":"scan","ok":true,"final":true,"networks":[["Kantine",82,1],["kapot"],["Gast",20,0]]}""".toByteArray()))
        assertEquals(listOf(WifiNetwork("Kantine", 82, true), WifiNetwork("Gast", 20, false)), scan.networks)
    }
}

@OptIn(ExperimentalCoroutinesApi::class)
class ManageClientTest {
    private val link = ManageLink.parse(STICKER_URL)!!

    private fun TestScope.make(vararg pis: MockManagePi): Pair<ManageClient, MockManageTransport> {
        val transport = MockManageTransport(pis.toList(), backgroundScope)
        return ManageClient(link, transport, backgroundScope) to transport
    }

    private fun TestScope.started(vararg pis: MockManagePi): Pair<ManageClient, MockManageTransport> {
        val (client, transport) = make(*pis)
        client.start()
        waitUntil { client.phase.value == ManagePhase.Status }
        return client to transport
    }

    /** Virtual time in small steps: the poller and the establish loop never go idle. */
    private fun TestScope.waitUntil(seconds: Long = 60, condition: () -> Boolean): Boolean {
        val deadline = currentTime + seconds * 1000
        while (!condition()) {
            if (currentTime > deadline) return false
            advanceTimeBy(10)
            runCurrent()
        }
        return true
    }

    private fun counters(frames: List<ByteArray>) = frames.map(ManageCrypto::counter)

    private val wrongPasswordFailure = ManagePhase.JoinFailed(
        "Verbinden met Office 5G mislukt: Verkeerd wachtwoord? Terug naar Kantine.",
        "Kantine",
    )

    @Test fun showsTheStatusOfTheScreen() = runTest {
        val pi = MockManagePi(VECTOR_SECRET)
        val (c, t) = started(pi)
        assertEquals(ManagePhase.Status, c.phase.value)
        val s = required(c.status.value)
        assertEquals("Kantine links", s.name)
        assertEquals("Kantine", s.ssid)
        assertEquals(82, s.signal)
        assertEquals("192.168.1.42", s.ip)
        assertEquals(true, s.online)
        assertEquals(true, s.paired)
        assertEquals("v0.9.7", s.agentVersion)
        assertEquals("pi-10000000a1b2c3d4", s.device)
        assertTrue(c.isConnected.value)
        assertEquals(listOf(1L), counters(t.requestFrames))
        assertEquals(listOf<ManageRequest>(ManageRequest.Status), pi.accepted)
        c.stop()
    }

    @Test fun triesTheStrongestCandidateFirstThenTheNext() = runTest {
        val near = MockManagePi(OTHER_SECRET, rssi = -40, device = "pi-near", name = "Buurman")
        val far = MockManagePi(VECTOR_SECRET, rssi = -75, device = "pi-far", name = "Kantine links")
        val (c, t) = started(far, near)
        assertEquals(ManagePhase.Status, c.phase.value)
        assertEquals("pi-far", c.status.value?.device)
        assertEquals(listOf(near.id, far.id), t.connects)
        assertTrue(near.accepted.isEmpty())
        c.stop()
    }

    @Test fun everyCandidateRejectingMeansTheQrExpired() = runTest {
        val rotated = MockManagePi(OTHER_SECRET, rssi = -50)
        val unpaired = MockManagePi(null, rssi = -60)
        val (c, t) = make(rotated, unpaired)
        c.start()
        assertTrue(waitUntil { c.phase.value == ManagePhase.Expired })
        assertEquals(listOf(rotated.id, unpaired.id), t.connects)
        c.stop()
    }

    @Test fun aNewerProtocolAsksForAnAppUpdate() = runTest {
        val pi = MockManagePi(VECTOR_SECRET).apply { version = 2 }
        val (c, _) = make(pi)
        c.start()
        assertTrue(waitUntil { c.phase.value == ManagePhase.Outdated })
        assertTrue(pi.accepted.isEmpty())
        c.stop()
    }

    @Test fun aPiWithoutManagementIsUnsupported() = runTest {
        val pi = MockManagePi(VECTOR_SECRET).apply { hasManagement = false }
        val (c, _) = make(pi)
        c.start()
        assertTrue(waitUntil { c.phase.value == ManagePhase.Unsupported })
        c.stop()
    }

    @Test fun aLockedPiIsAnExpiredSticker() = runTest {
        val pi = MockManagePi(null)
        val (c, _) = make(pi)
        c.start()
        assertTrue(waitUntil { c.phase.value == ManagePhase.Expired })
        assertTrue(pi.accepted.isEmpty())
        c.stop()
    }

    @Test fun malformedIsABugThenRetryStartsOver() = runTest {
        val pi = MockManagePi(VECTOR_SECRET).apply { malformNextRequest = true }
        val (c, _) = make(pi)
        c.start()
        assertTrue(waitUntil { c.phase.value == ManagePhase.Failed(ManageClient.BUG_MESSAGE) })
        c.retry()
        assertTrue(waitUntil { c.phase.value == ManagePhase.Status })
        c.stop()
    }

    @Test fun scansAndSwitchesNetworks() = runTest {
        val pi = MockManagePi(VECTOR_SECRET)
        val (c, _) = started(pi)
        c.chooseNetwork()
        assertEquals(ManagePhase.Choosing, c.phase.value)
        assertTrue(c.isScanning.value)
        assertTrue(waitUntil { !c.isScanning.value })
        assertEquals(listOf("Kantine", "Zeker Zichtbaar", "Office 5G", "Gast"), c.networks.value.map { it.ssid })

        c.connect("Office 5G", "geheim123", hidden = false)
        assertEquals(ManagePhase.Working("Verbinden met Office 5G…"), c.phase.value)
        assertTrue(waitUntil { c.phase.value == ManagePhase.Joined("Office 5G", online = true, message = null) })
        c.backToStatus()
        assertTrue(waitUntil { c.status.value?.ssid == "Office 5G" })
        assertEquals(ManagePhase.Status, c.phase.value)
        assertEquals(
            listOf(ManageRequest.Status, ManageRequest.Scan, ManageRequest.Connect("Office 5G", "geheim123", false), ManageRequest.Status),
            pi.accepted,
        )
        c.stop()
    }

    @Test fun progressUpdatesTheWorkingText() = runTest {
        val pi = MockManagePi(VECTOR_SECRET)
        val (c, _) = started(pi)
        c.connect("Gast", "", hidden = false)
        assertTrue(waitUntil { pi.job?.state == "busy" })
        assertEquals(ManagePhase.Working("Verbinden met Gast…"), c.phase.value)
        assertTrue(waitUntil { c.phase.value is ManagePhase.Joined })
        c.stop()
    }

    @Test fun aWrongPasswordFallsBackToTheOldNetwork() = runTest {
        val pi = MockManagePi(VECTOR_SECRET)
        val (c, _) = started(pi)
        c.connect("Office 5G", MockManagePi.WRONG_PASSWORD, hidden = false)
        assertTrue(waitUntil { c.phase.value == wrongPasswordFailure })
        c.backToList()
        assertEquals(ManagePhase.Choosing, c.phase.value)
        c.stop()
    }

    @Test fun anAnswerBeforeTheWriteCallbackStillCounts() = runTest {
        val pi = MockManagePi(VECTOR_SECRET)
        val (c, _) = started(pi)
        c.connect("Office 5G", "kort", hidden = false)
        assertTrue(waitUntil(5) { c.phase.value is ManagePhase.JoinFailed })
        assertEquals(ManagePhase.JoinFailed("Een wifi-wachtwoord is 8 tot 63 tekens.", "Kantine"), c.phase.value)
        c.stop()
    }

    @Test fun aChangedPasswordWhileOfflineIsFixed() = runTest {
        val pi = MockManagePi(VECTOR_SECRET).apply {
            ssid = null
            signal = null
            ip = null
            online = false
            network = null
        }
        val (c, _) = started(pi)
        assertEquals(false, c.status.value?.online)
        assertNull(c.status.value?.ssid)
        c.connect("Kantine", "nieuwgeheim", hidden = false)
        assertTrue(waitUntil { c.phase.value == ManagePhase.Joined("Kantine", online = true, message = null) })
        c.stop()
    }

    @Test fun theOutcomeArrivesViaStatusWhenTheLinkDropsMidJob() = runTest {
        val pi = MockManagePi(VECTOR_SECRET).apply { dropLinkDuringConnect = true }
        val (c, t) = started(pi)
        c.connect("Office 5G", "geheim123", hidden = false)
        assertTrue(waitUntil { c.isReconnecting.value })
        assertTrue(waitUntil { c.phase.value == ManagePhase.Joined("Office 5G", online = true, message = null) })
        assertFalse(c.isReconnecting.value)
        assertEquals(2, t.connects.size)
        c.stop()
    }

    @Test fun aFailureAfterADroppedLinkIsReportedToo() = runTest {
        val pi = MockManagePi(VECTOR_SECRET).apply { dropLinkDuringConnect = true }
        val (c, _) = started(pi)
        c.connect("Office 5G", MockManagePi.WRONG_PASSWORD, hidden = false)
        assertTrue(waitUntil { c.phase.value == wrongPasswordFailure })
        c.stop()
    }

    @Test fun anIdleSessionGetsOneNewHandshake() = runTest {
        val pi = MockManagePi(VECTOR_SECRET)
        val (c, t) = started(pi)
        pi.endSession()
        pi.ssid = "Gast"
        c.refreshStatus()
        assertEquals(ManagePhase.Status, c.phase.value)
        assertEquals("Gast", c.status.value?.ssid)
        assertEquals(listOf(1L, 2L, 1L), counters(t.requestFrames))
        assertEquals(1, t.connects.size)
        c.stop()
    }

    @Test fun aNewQrWhileManagingEndsInExpired() = runTest {
        val pi = MockManagePi(VECTOR_SECRET)
        val (c, t) = started(pi)
        pi.secret = OTHER_SECRET
        c.refreshStatus()
        assertEquals(ManagePhase.Expired, c.phase.value)
        assertEquals(listOf(1L, 2L, 1L), counters(t.requestFrames))
        assertEquals(listOf<ManageRequest>(ManageRequest.Status), pi.accepted)
        c.stop()
    }

    @Test fun requestCountersRiseAndACapturedConnectIsRejected() = runTest {
        val pi = MockManagePi(VECTOR_SECRET)
        val (c, t) = started(pi)
        c.connect("Office 5G", "geheim123", hidden = false)
        assertTrue(waitUntil { c.phase.value is ManagePhase.Joined })
        c.refreshStatus()
        assertEquals(listOf(1L, 2L, 3L), counters(t.requestFrames).take(3))
        val captured = t.requestFrames[1]
        val acceptedBefore = pi.accepted.size

        assertThrows(ManageLinkException.Rejected::class.java) { pi.requestWrite(captured) }
        pi.endSession()
        c.refreshStatus()
        assertThrows(ManageLinkException.Rejected::class.java) { pi.requestWrite(captured) }
        assertEquals(acceptedBefore + 1, pi.accepted.size)
        assertEquals(1, pi.accepted.count { it is ManageRequest.Connect })
        c.stop()
    }

    @Test fun aReplayedResponseIsIgnored() = runTest {
        val pi = MockManagePi(VECTOR_SECRET)
        val (c, t) = started(pi)
        val old = required(pi.latestResponse)
        pi.ssid = "Gast"
        c.refreshStatus()
        assertEquals("Gast", c.status.value?.ssid)
        t.onEvent?.invoke(ManageTransportEvent.Response(old, read = false))
        t.onEvent?.invoke(ManageTransportEvent.Response(old, read = true))
        assertEquals("Gast", c.status.value?.ssid)
        c.stop()
    }

    @Test fun aForeignNotificationTriggersARead() = runTest {
        val pi = MockManagePi(VECTOR_SECRET)
        val (c, t) = started(pi)
        val reads = t.responseReads
        val foreign = ManageCrypto.seal("""{"re":1}""".toByteArray(), ManageCrypto.randomNonce() + ManageCrypto.randomNonce(), Direction.RESPONSE, 9)
        t.onEvent?.invoke(ManageTransportEvent.Response(foreign, read = false))
        assertEquals(reads + 1, t.responseReads)
        t.onEvent?.invoke(ManageTransportEvent.Response(foreign, read = true))
        assertEquals(reads + 1, t.responseReads)
        c.stop()
    }

    @Test fun missedNotificationsAreRead() = runTest {
        val pi = MockManagePi(VECTOR_SECRET).apply { notifies = false }
        val (c, t) = started(pi)
        assertEquals(ManagePhase.Status, c.phase.value)
        assertEquals("Kantine links", c.status.value?.name)
        assertTrue(t.responseReads >= 1)
        c.stop()
    }

    @Test fun opsThePiDoesNotKnowAreHidden() = runTest {
        val pi = MockManagePi(VECTOR_SECRET).apply { ops = listOf("status") }
        val (c, _) = started(pi)
        assertEquals(ManagePhase.Status, c.phase.value)
        assertEquals(false, c.status.value?.canChangeWifi)
        c.stop()
    }

    @Test fun demoTransportAcceptsTheLink() = runTest {
        val c = ManageClient(link, MockManageTransport.demo(link, backgroundScope), backgroundScope)
        c.start()
        assertTrue(waitUntil { c.phase.value == ManagePhase.Status })
        c.stop()
    }
}
