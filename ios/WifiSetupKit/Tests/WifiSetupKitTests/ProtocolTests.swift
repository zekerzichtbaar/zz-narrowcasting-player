import CryptoKit
import Foundation
import Testing
@testable import WifiSetupKit

private let vectorURL = URL(string: "https://app.zekerzichtbaar.tv/setup?d=1E2F&k=AAECAwQFBgcICQoLDA0ODw")!
private let vectorToken = Data(0..<16)
private let pythonFrame = """
006a6465666768696a6b6c6d6e6f45150c51ba54031f54ea9ced86fde6690e21b3f420f07303c607ed9ecee16947936ae385d447dd15ad432c\
865fa840881365455532f7d1a25981ebd34d90150f39ccd63cb146f6738fefb08608a8f1a59fdc3a8d1d53306f5d04177cdb04
"""

private func hex(_ data: some Sequence<UInt8>) -> String {
    data.map { String(format: "%02x", $0) }.joined()
}

private func bytes(hex: String) -> Data {
    var out = Data()
    var i = hex.startIndex
    while i < hex.endIndex {
        let j = hex.index(i, offsetBy: 2)
        out.append(UInt8(hex[i..<j], radix: 16)!)
        i = j
    }
    return out
}

@Suite struct LinkTests {
    @Test func parsesTheVectorURL() throws {
        let link = try #require(SetupLink(url: vectorURL))
        #expect(link.deviceID == "1E2F")
        #expect(link.token == vectorToken)
        #expect(link.localName == "ZZ-1E2F")
    }

    @Test func normalizesTheDeviceIDToUpperCase() throws {
        let link = try #require(SetupLink(string: " https://x.test/setup?k=AAECAwQFBgcICQoLDA0ODw&d=1e2f \n"))
        #expect(link.deviceID == "1E2F")
    }

    @Test func recognizesTheDemoLink() throws {
        let demo = try #require(SetupLink(string: "https://app.zekerzichtbaar.tv/setup?d=demo&k=AAECAwQFBgcICQoLDA0ODw"))
        #expect(demo.isDemo)
        #expect(try #require(SetupLink(url: vectorURL)).isDemo == false)
    }

    @Test(arguments: [
        "https://app.zekerzichtbaar.tv/setup",
        "https://app.zekerzichtbaar.tv/setup?d=1E2F",
        "https://app.zekerzichtbaar.tv/setup?d=1E2&k=AAECAwQFBgcICQoLDA0ODw",
        "https://app.zekerzichtbaar.tv/setup?d=1E2F&k=AAECAwQFBgcICQoLDA0O",
        "https://app.zekerzichtbaar.tv/setup?d=1E-F&k=AAECAwQFBgcICQoLDA0ODw",
        "https://app.zekerzichtbaar.tv/setup?d=1E2F&k=!!!",
    ])
    func rejectsBrokenLinks(_ string: String) {
        #expect(SetupLink(string: string) == nil)
    }
}

@Suite struct CryptoTests {
    @Test func derivesThePythonKey() {
        let key = FrameCrypto.key(token: vectorToken).withUnsafeBytes { Data($0) }
        #expect(hex(key) == "3e61773816500fece50152798adfe908941f25fa47fdac077f18bf7a8b6b6a44")
    }

    @Test func opensThePythonFrame() throws {
        let plain = try FrameCrypto.open(bytes(hex: pythonFrame), token: vectorToken)
        #expect(Command(json: plain) == .connect(ssid: "Kantine", password: "geheim123", hidden: false))
    }

    @Test func sealsFramesThePiCanOpen() throws {
        let nonce = try AES.GCM.Nonce(data: bytes(hex: "6465666768696a6b6c6d6e6f"))
        let frame = try FrameCrypto.seal(.connect(ssid: "Kantine", password: "geheim123", hidden: false),
                                         token: vectorToken, nonce: nonce)
        #expect(Int(frame[0]) << 8 | Int(frame[1]) == frame.count - 2)
        #expect(frame.count <= SetupProtocol.maxValueLength)
        let plain = try FrameCrypto.open(frame, token: vectorToken)
        #expect(Command(json: plain) == .connect(ssid: "Kantine", password: "geheim123", hidden: false))
        if let path = ProcessInfo.processInfo.environment["SWIFT_FRAME_OUT"] {
            try hex(frame).write(toFile: path, atomically: true, encoding: .utf8)
        }
    }

    @Test func largestCommandFitsInOneValue() throws {
        let frame = try FrameCrypto.seal(.connect(ssid: String(repeating: "\"", count: 32),
                                                  password: String(repeating: "\\", count: 63), hidden: true),
                                         token: vectorToken)
        #expect(frame.count <= SetupProtocol.maxValueLength)
    }

    @Test func wrongTokenIsUnauthorized() throws {
        let frame = try FrameCrypto.seal(.scan, token: Data(repeating: 7, count: 16))
        #expect(throws: FrameError.unauthorized) { try FrameCrypto.open(frame, token: vectorToken) }
        #expect(throws: FrameError.badLength) { try FrameCrypto.open(frame.dropLast(), token: vectorToken) }
    }
}

@Suite struct PayloadTests {
    @Test func decodesStatus() throws {
        let json = #"{"v":1,"seq":7,"state":"online","msg":null,"ssid":"Kantine","scan":3,"code":"K7Q-4M2","url":"https://cms/schermen?code=K7Q-4M2"}"#
        let status = try #require(SetupStatus.decode(Data(json.utf8)))
        #expect(status.state == .online && status.seq == 7 && status.scan == 3)
        #expect(status.code == "K7Q-4M2" && status.url?.host == "cms" && status.ssid == "Kantine")
    }

    @Test func toleratesUnknownStatesAndTruncation() {
        #expect(SetupStatus.decode(Data(#"{"v":2,"seq":1,"state":"rebooting","scan":0}"#.utf8))?.state == .unknown)
        #expect(SetupStatus.decode(Data(#"{"v":1,"seq":7,"state":"fail"#.utf8)) == nil)
    }

    @Test func decodesNetworks() throws {
        let list = try #require(WifiNetwork.decodeList(Data(#"[["Kantine",82,1],["Gast",20,0],["kapot"]]"#.utf8)))
        #expect(list == [WifiNetwork(ssid: "Kantine", signal: 82, secured: true),
                         WifiNetwork(ssid: "Gast", signal: 20, secured: false)])
        #expect(WifiNetwork.decodeList(Data("[]".utf8)) == [])
    }

    @Test func validatesLikeThePi() {
        #expect(Validation.password("", secured: false) == nil)
        #expect(Validation.password("", secured: true) != nil)
        #expect(Validation.password("kort", secured: true) != nil)
        #expect(Validation.password("geheim123", secured: true) == nil)
        #expect(Validation.password(String(repeating: "x", count: 64), secured: true) != nil)
        #expect(Validation.ssid(String(repeating: "é", count: 17)) != nil)
        #expect(Validation.ssid("  ") != nil)
    }
}

@MainActor
@Suite struct ClientTests {
    final class FakeTransport: SetupTransport {
        var onEvent: ((TransportEvent) -> Void)?
        var autoReconnect = true
        var sent: [Command] = []
        func start(link: SetupLink) {}
        func stop() {}
        func send(_ frame: Data) {
            sent.append(Command(json: try! FrameCrypto.open(frame, token: vectorToken))!)
        }
        func emit(_ event: TransportEvent) { onEvent?(event) }
    }

    private func client() -> (SetupClient, FakeTransport) {
        let transport = FakeTransport()
        return (SetupClient(link: SetupLink(url: vectorURL)!, transport: transport), transport)
    }

    @Test func connectThenWrongPasswordThenOnline() {
        let (c, t) = client()
        t.emit(.connecting)
        t.emit(.status(SetupStatus(seq: 1, state: .ready, scan: 1)))
        #expect(c.phase == .choosing)
        c.connect(ssid: "Kantine", password: "fout1234", hidden: false)
        #expect(t.sent == [.connect(ssid: "Kantine", password: "fout1234", hidden: false)])
        t.emit(.status(SetupStatus(seq: 2, state: .busy, msg: "Verbinden met Kantine…", scan: 1)))
        #expect(c.phase == .working("Verbinden met Kantine…"))
        t.emit(.status(SetupStatus(seq: 3, state: .ready, scan: 1)))
        #expect(c.phase == .working("Verbinden met Kantine…"))
        t.emit(.status(SetupStatus(seq: 4, state: .failed, msg: "Verkeerd wachtwoord?", scan: 1)))
        #expect(c.phase == .failed("Verkeerd wachtwoord?"))
        c.backToList()
        #expect(c.phase == .choosing && c.notice == nil)

        c.connect(ssid: "Kantine", password: "geheim123", hidden: false)
        t.emit(.status(SetupStatus(seq: 4, state: .failed, msg: "Verkeerd wachtwoord?", scan: 1)))
        #expect(c.phase == .working("Verbinden met Kantine…"))
        t.emit(.status(SetupStatus(seq: 5, state: .online, ssid: "Kantine", scan: 1)))
        #expect(c.phase == .online(ssid: "Kantine", code: nil, url: nil))
        #expect(!t.autoReconnect)
        t.emit(.disconnected)
        #expect(c.phase.isFinished)
    }

    @Test func oldFailureIsANoticeNotAFailureScreen() {
        let (c, t) = client()
        t.emit(.status(SetupStatus(seq: 9, state: .failed, msg: "Netwerk niet bereikbaar.", scan: 1)))
        #expect(c.phase == .choosing && c.notice == "Netwerk niet bereikbaar.")
    }

    @Test func scanKeepsTheListUntilANewScanID() {
        let (c, t) = client()
        t.emit(.status(SetupStatus(seq: 1, state: .ready, scan: 1)))
        c.rescan()
        #expect(t.sent == [.scan] && c.isScanning)
        t.emit(.status(SetupStatus(seq: 2, state: .busy, msg: "Netwerken zoeken…", scan: 1)))
        #expect(c.phase == .choosing && c.isScanning)
        t.emit(.status(SetupStatus(seq: 3, state: .ready, scan: 1)))
        #expect(c.isScanning)
        t.emit(.status(SetupStatus(seq: 4, state: .ready, scan: 2)))
        #expect(!c.isScanning && c.phase == .choosing)
    }

    @Test func expiredTokenStopsTheFlow() {
        let (c, t) = client()
        t.emit(.status(SetupStatus(seq: 1, state: .ready, scan: 1)))
        c.connect(ssid: "Kantine", password: "geheim123", hidden: false)
        t.emit(.writeExpired)
        #expect(c.phase == .expired)
        t.emit(.status(SetupStatus(seq: 2, state: .ready, scan: 1)))
        #expect(c.phase == .expired)
    }

    @Test func searchingBecomesSlow() {
        let (c, t) = client()
        t.emit(.searching)
        t.emit(.notFoundYet)
        #expect(c.phase == .searching(slow: true))
        t.emit(.bluetooth(.off))
        #expect(c.phase == .bluetooth(.off))
    }
}

@MainActor
@Suite struct MockTests {
    @Test func mockWalksTheWholeFlow() async throws {
        let client = SetupClient(link: SetupLink(url: vectorURL)!, transport: MockTransport(speed: 50))
        client.start()
        try await Task.sleep(for: .milliseconds(200))
        #expect(client.phase == .choosing && client.networks.count == 4)
        client.connect(ssid: "Kantine", password: MockTransport.wrongPassword, hidden: false)
        try await Task.sleep(for: .milliseconds(200))
        #expect(client.phase == .failed("Verbinden met Kantine mislukt: Verkeerd wachtwoord?"))
        client.backToList()
        client.connect(ssid: "Kantine", password: "geheim123", hidden: false)
        try await Task.sleep(for: .milliseconds(300))
        #expect(client.phase == .online(ssid: "Kantine", code: "K7Q-4M2", url: MockTransport.pairingURL))
    }
}
