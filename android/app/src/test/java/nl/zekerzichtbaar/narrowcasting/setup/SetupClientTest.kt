package nl.zekerzichtbaar.narrowcasting.setup

import kotlin.time.Duration.Companion.seconds
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

private class FakeTransport : SetupTransport {
    override var onEvent: ((TransportEvent) -> Unit)? = null
    override var autoReconnect = true
    override val usesBluetooth = false
    val sent = mutableListOf<Command>()
    override fun start(link: SetupLink) {}
    override fun stop() {}
    override fun send(frame: ByteArray) {
        sent += Command.fromJson(FrameCrypto.open(frame, vectorToken))!!
    }
    fun emit(event: TransportEvent) = onEvent?.invoke(event)
}

private fun status(seq: Int, state: SetupStatus.State, msg: String? = null, ssid: String? = null, scan: Int = 1) =
    TransportEvent.Status(SetupStatus(seq = seq, state = state, msg = msg, ssid = ssid, scan = scan))

@OptIn(ExperimentalCoroutinesApi::class)
class SetupClientTest {
    private val link = SetupLink.parse(VECTOR_URL)!!

    @Test fun connectThenWrongPasswordThenOnline() = runTest {
        val t = FakeTransport()
        val c = SetupClient(link, t, backgroundScope)
        t.emit(TransportEvent.Connecting)
        t.emit(status(1, SetupStatus.State.READY))
        assertEquals(Phase.Choosing, c.phase.value)
        c.connect("Kantine", "fout1234", hidden = false)
        assertEquals(listOf(Command.Connect("Kantine", "fout1234", false)), t.sent)
        t.emit(status(2, SetupStatus.State.BUSY, msg = "Verbinden met Kantine…"))
        assertEquals(Phase.Working("Verbinden met Kantine…"), c.phase.value)
        t.emit(status(3, SetupStatus.State.READY))
        assertEquals(Phase.Working("Verbinden met Kantine…"), c.phase.value)
        t.emit(status(4, SetupStatus.State.FAILED, msg = "Verkeerd wachtwoord?"))
        assertEquals(Phase.Failed("Verkeerd wachtwoord?"), c.phase.value)
        c.backToList()
        assertEquals(Phase.Choosing, c.phase.value)
        assertNull(c.notice.value)

        c.connect("Kantine", "geheim123", hidden = false)
        t.emit(status(4, SetupStatus.State.FAILED, msg = "Verkeerd wachtwoord?"))
        assertEquals(Phase.Working("Verbinden met Kantine…"), c.phase.value)
        t.emit(status(5, SetupStatus.State.ONLINE, ssid = "Kantine"))
        assertEquals(Phase.Online("Kantine", null, null), c.phase.value)
        assertFalse(t.autoReconnect)
        t.emit(TransportEvent.Disconnected)
        assertTrue(c.phase.value.isFinished)
    }

    @Test fun oldFailureIsANoticeNotAFailureScreen() = runTest {
        val t = FakeTransport()
        val c = SetupClient(link, t, backgroundScope)
        t.emit(status(9, SetupStatus.State.FAILED, msg = "Netwerk niet bereikbaar."))
        assertEquals(Phase.Choosing, c.phase.value)
        assertEquals("Netwerk niet bereikbaar.", c.notice.value)
    }

    @Test fun scanKeepsTheListUntilANewScanId() = runTest {
        val t = FakeTransport()
        val c = SetupClient(link, t, backgroundScope)
        t.emit(status(1, SetupStatus.State.READY))
        c.rescan()
        assertEquals(listOf<Command>(Command.Scan), t.sent)
        assertTrue(c.isScanning.value)
        t.emit(status(2, SetupStatus.State.BUSY, msg = "Netwerken zoeken…"))
        assertEquals(Phase.Choosing, c.phase.value)
        assertTrue(c.isScanning.value)
        t.emit(status(3, SetupStatus.State.READY))
        assertTrue(c.isScanning.value)
        t.emit(status(4, SetupStatus.State.READY, scan = 2))
        assertFalse(c.isScanning.value)
        assertEquals(Phase.Choosing, c.phase.value)
    }

    @Test fun expiredTokenStopsTheFlow() = runTest {
        val t = FakeTransport()
        val c = SetupClient(link, t, backgroundScope)
        t.emit(status(1, SetupStatus.State.READY))
        c.connect("Kantine", "geheim123", hidden = false)
        t.emit(TransportEvent.WriteExpired)
        assertEquals(Phase.Expired, c.phase.value)
        t.emit(status(2, SetupStatus.State.READY))
        assertEquals(Phase.Expired, c.phase.value)
    }

    @Test fun searchingBecomesSlow() = runTest {
        val t = FakeTransport()
        val c = SetupClient(link, t, backgroundScope)
        t.emit(TransportEvent.Searching)
        t.emit(TransportEvent.NotFoundYet)
        assertEquals(Phase.Searching(slow = true), c.phase.value)
        t.emit(TransportEvent.Bluetooth(BluetoothProblem.OFF))
        assertEquals(Phase.Bluetooth(BluetoothProblem.OFF), c.phase.value)
    }

    @Test fun silentScreenTimesOut() = runTest {
        val t = FakeTransport()
        val c = SetupClient(link, t, backgroundScope, commandTimeout = 75.seconds)
        t.emit(status(1, SetupStatus.State.READY))
        c.connect("Kantine", "geheim123", hidden = false)
        advanceTimeBy(74.seconds)
        assertEquals(Phase.Working("Verbinden met Kantine…"), c.phase.value)
        advanceTimeBy(2.seconds)
        assertEquals(Phase.Failed("Het scherm reageert niet. Probeer het opnieuw."), c.phase.value)
    }

    @Test fun mockWalksTheWholeFlow() = runTest {
        val client = SetupClient(link, MockTransport(backgroundScope, speed = 50.0), backgroundScope)
        client.start()
        advanceTimeBy(200)
        runCurrent()
        assertEquals(Phase.Choosing, client.phase.value)
        assertEquals(4, client.networks.value.size)
        client.connect("Kantine", MockTransport.WRONG_PASSWORD, hidden = false)
        advanceTimeBy(200)
        runCurrent()
        assertEquals(Phase.Failed("Verbinden met Kantine mislukt: Verkeerd wachtwoord?"), client.phase.value)
        client.backToList()
        client.connect("Kantine", "geheim123", hidden = false)
        advanceTimeBy(300)
        runCurrent()
        assertEquals(Phase.Online("Kantine", "K7Q-4M2", MockTransport.PAIRING_URL), client.phase.value)
    }
}
