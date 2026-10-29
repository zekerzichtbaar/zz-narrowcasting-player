import CryptoKit
import Foundation
import Testing
@testable import WifiSetupKit

private let vectorSecret = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"
private let vectorNA = Data(0..<16)
private let vectorNP = Data(16..<32)
private let vectorKs = "58cb3bf3146d29f20289f4397eefeb0b283e00efab27c34056d96b894cbfa048"
private let vectorRequest = "0023000000016d4aebd66f2afc6e5d9fc7246f979f09d6a1a87cbd131eea5cef3524343cb0"
private let vectorResponse = """
004d00000001e9c9fbace569fc061f9e768a3bcd4adf7f064c9c5bf019ac29e316286054658ff8bb44cef05cac73d8c22bf66f10d8b2f7b44103a78bb7bedd0efa4219f3963c9eff9905b6022eeed5
"""
private let stickerURL = "https://app.zekerzichtbaar.tv/setup?d=1E2F&s=\(vectorSecret)"
private let otherSecret = "ZYXWVUTSRQPONMLKJIHGFEDCBA9876543210zyxwvut"

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

private var vectorKey: SymmetricKey {
    ManageCrypto.sessionKey(secret: vectorSecret, na: vectorNA, np: vectorNP)
}

@Suite struct ScreenLinkTests {
    @Test func stickerOpensManagement() throws {
        guard case let .manage(link) = try #require(ScreenLink(string: stickerURL)) else {
            Issue.record("not a management link")
            return
        }
        #expect(link.deviceID == "1E2F" && link.secret == vectorSecret && link.localName == "ZZ-1E2F")
        #expect(!link.isDemo)
        #expect(!String(describing: link).contains(vectorSecret))
    }

    @Test func tvQROpensSetup() throws {
        let link = try #require(ScreenLink(string: "https://app.zekerzichtbaar.tv/setup?d=1e2f&k=AAECAwQFBgcICQoLDA0ODw"))
        guard case let .setup(setup) = link else {
            Issue.record("not a setup link")
            return
        }
        #expect(setup.deviceID == "1E2F" && setup.token == Data(0..<16))
    }

    @Test func setupWinsWhenBothArePresent() throws {
        let both = try #require(ScreenLink(string: "\(stickerURL)&k=AAECAwQFBgcICQoLDA0ODw"))
        guard case .setup = both else {
            Issue.record("k must win")
            return
        }
    }

    @Test func aBrokenTokenNeverFallsThroughToManagement() {
        #expect(ScreenLink(string: "\(stickerURL)&k=kapot") == nil)
    }

    @Test func demoStickerIsRecognized() throws {
        let link = try #require(ManageLink(string: "https://app.zekerzichtbaar.tv/setup?d=demo&s=\(vectorSecret)"))
        #expect(link.isDemo && link.deviceID == "DEMO")
    }

    @Test func secretLengthLimitsMatchThePi() {
        #expect(ManageLink(string: "https://x.test/setup?d=1E2F&s=\(String(repeating: "a", count: 22))") != nil)
        #expect(ManageLink(string: "https://x.test/setup?d=1E2F&s=\(String(repeating: "a", count: 128))") != nil)
        #expect(ManageLink(string: "https://x.test/setup?d=1E2F&s=\(String(repeating: "a", count: 21))") == nil)
        #expect(ManageLink(string: "https://x.test/setup?d=1E2F&s=\(String(repeating: "a", count: 129))") == nil)
    }

    @Test(arguments: [
        "https://app.zekerzichtbaar.tv/setup",
        "https://app.zekerzichtbaar.tv/setup?d=1E2F",
        "https://app.zekerzichtbaar.tv/setup?s=\(vectorSecret)",
        "https://app.zekerzichtbaar.tv/setup?d=1E2&s=\(vectorSecret)",
        "https://app.zekerzichtbaar.tv/setup?d=1E2FF&s=\(vectorSecret)",
        "https://app.zekerzichtbaar.tv/setup?d=1E-F&s=\(vectorSecret)",
        "https://app.zekerzichtbaar.tv/setup?d=1E2F&s=0123456789abcdefghij+lmnopqrstuvwxyz",
        "https://app.zekerzichtbaar.tv/setup?d=1E2F&s=0123456789abcdefghijklmnopqrstuvwxyzABCDEF%3D",
    ])
    func rejectsBrokenStickers(_ string: String) {
        #expect(ScreenLink(string: string) == nil)
    }
}

@Suite struct ManageCryptoTests {
    @Test func derivesTheSpecSessionKey() {
        #expect(hex(vectorKey.withUnsafeBytes { Data($0) }) == vectorKs)
    }

    @Test func sealsTheSpecRequest() throws {
        let raw = try ManageCrypto.seal(Data(#"{"op":"status"}"#.utf8), key: vectorKey, direction: .request, ctr: 1)
        #expect(hex(raw) == vectorRequest)
        let encoded = try ManageCrypto.seal(ManageRequest.status.json(), key: vectorKey, direction: .request, ctr: 1)
        #expect(hex(encoded) == vectorRequest)
    }

    @Test func opensTheSpecResponse() throws {
        let (ctr, plain) = try ManageCrypto.open(bytes(hex: vectorResponse), key: vectorKey, direction: .response)
        #expect(ctr == 1)
        #expect(String(decoding: plain, as: UTF8.self) == #"{"re":1,"op":"scan","ok":true,"final":true,"networks":[]}"#)
        let response = try #require(ManageResponse.decode(plain))
        #expect(response.re == 1 && response.op == "scan" && response.ok == true && response.final)
        #expect(response.networks == [])
    }

    @Test func directionsNeverShareANonce() throws {
        #expect(throws: FrameError.unauthorized) {
            try ManageCrypto.open(bytes(hex: vectorResponse), key: vectorKey, direction: .request)
        }
        #expect(throws: FrameError.unauthorized) {
            try ManageCrypto.open(bytes(hex: vectorRequest), key: vectorKey, direction: .response)
        }
    }

    @Test func anotherSessionOrSecretCannotOpenIt() throws {
        let otherSession = ManageCrypto.sessionKey(secret: vectorSecret, na: vectorNP, np: vectorNA)
        let otherSticker = ManageCrypto.sessionKey(secret: otherSecret, na: vectorNA, np: vectorNP)
        for key in [otherSession, otherSticker] {
            #expect(throws: FrameError.unauthorized) {
                try ManageCrypto.open(bytes(hex: vectorRequest), key: key, direction: .request)
            }
        }
        #expect(throws: FrameError.badLength) {
            try ManageCrypto.open(bytes(hex: vectorRequest).dropLast(), key: vectorKey, direction: .request)
        }
        #expect(throws: FrameError.badLength) {
            try ManageCrypto.open(Data([0, 4, 0, 0, 0, 1]), key: vectorKey, direction: .request)
        }
    }

    @Test func largestConnectFitsInOneValue() throws {
        let request = ManageRequest.connect(ssid: String(repeating: "\"", count: 32),
                                            password: String(repeating: "\\", count: 63), hidden: true)
        let frame = try ManageCrypto.seal(request.json(), key: vectorKey, direction: .request, ctr: .max)
        #expect(frame.count <= ManageProtocol.maxValueLength)
    }

    /// `SWIFT_MANAGE_FRAME_OUT=<file>`: a frame for `blemanage.unseal` to open (Swift -> Python).
    @Test func sealsAConnectThePiCanOpen() throws {
        let request = ManageRequest.connect(ssid: "Kantine", password: "geheim123", hidden: false)
        let frame = try ManageCrypto.seal(request.json(), key: vectorKey, direction: .request, ctr: 2)
        let (ctr, plain) = try ManageCrypto.open(frame, key: vectorKey, direction: .request)
        #expect(ctr == 2 && ManageRequest(json: plain) == request)
        if let path = ProcessInfo.processInfo.environment["SWIFT_MANAGE_FRAME_OUT"] {
            try hex(frame).write(toFile: path, atomically: true, encoding: .utf8)
        }
    }
}

@Suite struct ManagePayloadTests {
    @Test func decodesTheSpecStatus() throws {
        let json = """
        {"re": 1, "op": "status", "ok": true, "final": true, "v": 1, "ops": ["status", "scan", "connect"],
         "device": "pi-10000000a1b2c3d4", "name": "Kantine links", "agent_version": "v0.9.7",
         "state": "PLAYING", "paired": true, "online": true,
         "network": "wifi", "ssid": "Kantine", "signal": 82, "ip": "192.168.1.42", "later": {"x": 1},
         "job": {"op": "connect", "state": "failed", "ssid": "Gast", "msg": "Verbinden met Gast mislukt: verkeerd wachtwoord?"}}
        """
        let status = ManageStatus(try #require(ManageResponse.decode(Data(json.utf8))))
        #expect(status.name == "Kantine links" && status.device == "pi-10000000a1b2c3d4")
        #expect(status.agentVersion == "v0.9.7" && status.screenState == "PLAYING")
        #expect(status.paired == true && status.online == true && status.network == "wifi")
        #expect(status.ssid == "Kantine" && status.signal == 82 && status.ip == "192.168.1.42")
        #expect(status.job == ManageJob(op: "connect", state: "failed", ssid: "Gast",
                                        msg: "Verbinden met Gast mislukt: verkeerd wachtwoord?"))
        #expect(status.canChangeWifi)
    }

    @Test func toleratesNullsAndMissingOps() throws {
        let json = #"{"re":4,"op":"status","ok":true,"final":true,"ops":["status"],"name":null,"online":null,"ssid":null,"signal":null,"ip":null,"job":null}"#
        let status = ManageStatus(try #require(ManageResponse.decode(Data(json.utf8))))
        #expect(status.name == nil && status.online == nil && status.ssid == nil && status.job == nil)
        #expect(!status.canChangeWifi)
    }

    @Test func decodesProgressAndNetworks() throws {
        let progress = try #require(ManageResponse.decode(Data(#"{"re":2,"op":"connect","final":false,"state":"busy","msg":"Verbinden met Gast…"}"#.utf8)))
        #expect(!progress.final && progress.state == "busy" && progress.ok == nil)
        let scan = try #require(ManageResponse.decode(Data(#"{"re":3,"op":"scan","ok":true,"final":true,"networks":[["Kantine",82,1],["kapot"],["Gast",20,0]]}"#.utf8)))
        #expect(scan.networks == [WifiNetwork(ssid: "Kantine", signal: 82, secured: true),
                                  WifiNetwork(ssid: "Gast", signal: 20, secured: false)])
    }
}

@MainActor
private func waitUntil(_ seconds: Double = 5, _ condition: () -> Bool) async -> Bool {
    let deadline = Date().addingTimeInterval(seconds)
    while !condition() {
        if Date() > deadline { return false }
        try? await Task.sleep(for: .milliseconds(5))
    }
    return true
}

@MainActor
private func requestCounters(_ frames: [Data]) -> [UInt32] {
    frames.map { $0[2..<6].reduce(UInt32(0)) { $0 << 8 | UInt32($1) } }
}

@MainActor
@Suite(.serialized) struct ManageClientTests {
    private let link = ManageLink(string: stickerURL)!
    private let speed = 100.0

    private func make(_ pis: [MockManagePi]) -> (ManageClient, MockManageTransport) {
        let transport = MockManageTransport(pis: pis, speed: speed)
        let client = ManageClient(link: link, transport: transport, timing: .standard.scaled(by: 50))
        return (client, transport)
    }

    private func started(_ pis: [MockManagePi]) async -> (ManageClient, MockManageTransport) {
        let (client, transport) = make(pis)
        client.start()
        _ = await waitUntil { client.phase == .status }
        return (client, transport)
    }

    @Test func showsTheStatusOfTheScreen() async {
        let pi = MockManagePi(secret: vectorSecret)
        let (c, t) = await started([pi])
        defer { c.stop() }
        #expect(c.phase == .status)
        #expect(c.status?.name == "Kantine links" && c.status?.ssid == "Kantine" && c.status?.signal == 82)
        #expect(c.status?.ip == "192.168.1.42" && c.status?.online == true && c.status?.paired == true)
        #expect(c.status?.agentVersion == "v0.9.7" && c.status?.device == "pi-10000000a1b2c3d4")
        #expect(requestCounters(t.requestFrames) == [1])
        #expect(pi.accepted == [.status])
    }

    @Test func triesTheStrongestCandidateFirstThenTheNext() async {
        let near = MockManagePi(secret: otherSecret, rssi: -40, device: "pi-near", name: "Buurman")
        let far = MockManagePi(secret: vectorSecret, rssi: -75, device: "pi-far", name: "Kantine links")
        let (c, t) = await started([far, near])
        defer { c.stop() }
        #expect(c.phase == .status && c.status?.device == "pi-far")
        #expect(t.connects == [near.id, far.id])
        #expect(near.accepted.isEmpty)
    }

    @Test func everyCandidateRejectingMeansTheQRExpired() async {
        let rotated = MockManagePi(secret: otherSecret, rssi: -50)
        let unpaired = MockManagePi(secret: nil, rssi: -60)
        let (c, t) = make([rotated, unpaired])
        defer { c.stop() }
        c.start()
        #expect(await waitUntil { c.phase == .expired })
        #expect(t.connects == [rotated.id, unpaired.id])
    }

    @Test func aNewerProtocolAsksForAnAppUpdate() async {
        let pi = MockManagePi(secret: vectorSecret)
        pi.version = 2
        let (c, _) = make([pi])
        defer { c.stop() }
        c.start()
        #expect(await waitUntil { c.phase == .outdated })
        #expect(pi.accepted.isEmpty)
    }

    @Test func aPiWithoutManagementIsUnsupported() async {
        let pi = MockManagePi(secret: vectorSecret)
        pi.hasManagement = false
        let (c, _) = make([pi])
        defer { c.stop() }
        c.start()
        #expect(await waitUntil { c.phase == .unsupported })
    }

    @Test func malformedIsABugThenRetryStartsOver() async {
        let pi = MockManagePi(secret: vectorSecret)
        pi.malformNextRequest = true
        let (c, _) = make([pi])
        defer { c.stop() }
        c.start()
        #expect(await waitUntil { c.phase == .failed(ManageClient.bugMessage) })
        c.retry()
        #expect(await waitUntil { c.phase == .status })
    }

    @Test func scansAndSwitchesNetworks() async {
        let pi = MockManagePi(secret: vectorSecret)
        let (c, _) = await started([pi])
        defer { c.stop() }
        c.chooseNetwork()
        #expect(c.phase == .choosing && c.isScanning)
        #expect(await waitUntil { !c.isScanning })
        #expect(c.networks.map(\.ssid) == ["Kantine", "Zeker Zichtbaar", "Office 5G", "Gast"])

        c.connect(ssid: "Office 5G", password: "geheim123", hidden: false)
        #expect(c.phase == .working("Verbinden met Office 5G…"))
        #expect(await waitUntil { c.phase == .joined(ssid: "Office 5G", online: true, message: nil) })
        c.backToStatus()
        #expect(await waitUntil { c.status?.ssid == "Office 5G" })
        #expect(c.phase == .status)
        #expect(pi.accepted == [.status, .scan, .connect(ssid: "Office 5G", password: "geheim123", hidden: false), .status])
    }

    @Test func aWrongPasswordFallsBackToTheOldNetwork() async {
        let pi = MockManagePi(secret: vectorSecret)
        let (c, _) = await started([pi])
        defer { c.stop() }
        c.connect(ssid: "Office 5G", password: MockManagePi.wrongPassword, hidden: false)
        #expect(await waitUntil {
            c.phase == .joinFailed(message: "Verbinden met Office 5G mislukt: Verkeerd wachtwoord? Terug naar Kantine.",
                                   ssid: "Kantine")
        })
    }

    @Test func aChangedPasswordWhileOfflineIsFixed() async {
        let pi = MockManagePi(secret: vectorSecret)
        (pi.ssid, pi.signal, pi.ip, pi.online, pi.network) = (nil, nil, nil, false, nil)
        let (c, _) = await started([pi])
        defer { c.stop() }
        #expect(c.status?.online == false && c.status?.ssid == nil)
        c.connect(ssid: "Kantine", password: "nieuwgeheim", hidden: false)
        #expect(await waitUntil { c.phase == .joined(ssid: "Kantine", online: true, message: nil) })
    }

    @Test func theOutcomeArrivesViaStatusWhenTheLinkDropsMidJob() async {
        let pi = MockManagePi(secret: vectorSecret)
        pi.dropLinkDuringConnect = true
        let (c, t) = await started([pi])
        defer { c.stop() }
        c.connect(ssid: "Office 5G", password: "geheim123", hidden: false)
        #expect(await waitUntil { c.isReconnecting })
        #expect(await waitUntil { c.phase == .joined(ssid: "Office 5G", online: true, message: nil) })
        #expect(!c.isReconnecting && t.connects.count == 2)
    }

    @Test func aFailureAfterADroppedLinkIsReportedToo() async {
        let pi = MockManagePi(secret: vectorSecret)
        pi.dropLinkDuringConnect = true
        let (c, _) = await started([pi])
        defer { c.stop() }
        c.connect(ssid: "Office 5G", password: MockManagePi.wrongPassword, hidden: false)
        #expect(await waitUntil {
            c.phase == .joinFailed(message: "Verbinden met Office 5G mislukt: Verkeerd wachtwoord? Terug naar Kantine.",
                                   ssid: "Kantine")
        })
    }

    @Test func anIdleSessionGetsOneNewHandshake() async {
        let pi = MockManagePi(secret: vectorSecret)
        let (c, t) = await started([pi])
        defer { c.stop() }
        pi.endSession()
        pi.ssid = "Gast"
        await c.refreshStatus()
        #expect(c.phase == .status && c.status?.ssid == "Gast")
        #expect(requestCounters(t.requestFrames) == [1, 2, 1])
        #expect(t.connects.count == 1)
    }

    @Test func aNewQRWhileManagingEndsInExpired() async {
        let pi = MockManagePi(secret: vectorSecret)
        let (c, t) = await started([pi])
        defer { c.stop() }
        pi.secret = otherSecret
        await c.refreshStatus()
        #expect(c.phase == .expired)
        #expect(requestCounters(t.requestFrames) == [1, 2, 1])
        #expect(pi.accepted == [.status])
    }

    @Test func requestCountersRiseAndACapturedConnectIsRejected() async throws {
        let pi = MockManagePi(secret: vectorSecret)
        let (c, t) = await started([pi])
        defer { c.stop() }
        c.connect(ssid: "Office 5G", password: "geheim123", hidden: false)
        #expect(await waitUntil { if case .joined = c.phase { true } else { false } })
        await c.refreshStatus()
        #expect(requestCounters(t.requestFrames).prefix(3) == [1, 2, 3])
        let captured = t.requestFrames[1]
        let acceptedBefore = pi.accepted.count

        #expect(throws: ManageLinkError.rejected) { try pi.requestWrite(captured) }
        pi.endSession()
        await c.refreshStatus()
        #expect(throws: ManageLinkError.rejected) { try pi.requestWrite(captured) }
        #expect(pi.accepted.count == acceptedBefore + 1)
        #expect(pi.accepted.filter { if case .connect = $0 { true } else { false } }.count == 1)
    }

    @Test func aReplayedResponseIsIgnored() async throws {
        let pi = MockManagePi(secret: vectorSecret)
        let (c, t) = await started([pi])
        defer { c.stop() }
        let old = try #require(pi.latestResponse)
        pi.ssid = "Gast"
        await c.refreshStatus()
        #expect(c.status?.ssid == "Gast")
        t.onEvent?(.response(old, read: false))
        t.onEvent?(.response(old, read: true))
        #expect(c.status?.ssid == "Gast")
    }

    @Test func aForeignNotificationTriggersARead() async {
        let pi = MockManagePi(secret: vectorSecret)
        let (c, t) = await started([pi])
        defer { c.stop() }
        let reads = t.responseReads
        let foreign = try! ManageCrypto.seal(Data(#"{"re":1}"#.utf8), key: SymmetricKey(size: .bits256),
                                             direction: .response, ctr: 9)
        t.onEvent?(.response(foreign, read: false))
        #expect(t.responseReads == reads + 1)
        t.onEvent?(.response(foreign, read: true))
        #expect(t.responseReads == reads + 1)
    }

    @Test func missedNotificationsAreRead() async {
        let pi = MockManagePi(secret: vectorSecret)
        pi.notifies = false
        let (c, t) = await started([pi])
        defer { c.stop() }
        #expect(c.phase == .status && c.status?.name == "Kantine links")
        #expect(t.responseReads >= 1)
    }

    @Test func opsThePiDoesNotKnowAreHidden() async {
        let pi = MockManagePi(secret: vectorSecret)
        pi.ops = ["status"]
        let (c, _) = await started([pi])
        defer { c.stop() }
        #expect(c.phase == .status && c.status?.canChangeWifi == false)
    }

    @Test func demoTransportAcceptsTheLink() async {
        let c = ManageClient(link: link, transport: MockManageTransport.demo(link: link, speed: speed),
                             timing: .standard.scaled(by: 50))
        defer { c.stop() }
        c.start()
        #expect(await waitUntil { c.phase == .status })
    }
}
