import CryptoKit
import Foundation

/// A simulated Pi's management service with the real crypto and the rules of
/// `pi_player/blemanage.py`: 0x08 without a secret, a session or a fresh `ctr`; the final of a job
/// only reaches the session that started it. Password "fout1234" fails and falls back.
@MainActor
public final class MockManagePi {
    public static let wrongPassword = "fout1234"

    public let id = UUID()
    public var secret: String? {
        didSet { if secret != oldValue { session = nil } }
    }
    public var rssi: Int
    public var version = ManageProtocol.version
    public var device: String
    public var name: String?
    public var agentVersion = "v0.9.7"
    public var screenState = "PLAYING"
    public var paired = true
    public var online: Bool? = true
    public var network: String? = "wifi"
    public var ssid: String? = "Kantine"
    public var signal: Int? = 82
    public var ip: String? = "192.168.1.42"
    public var ops = ["status", "scan", "connect"]
    public var networks: [WifiNetwork] = [
        WifiNetwork(ssid: "Kantine", signal: 82, secured: true),
        WifiNetwork(ssid: "Zeker Zichtbaar", signal: 74, secured: true),
        WifiNetwork(ssid: "Office 5G", signal: 54, secured: true),
        WifiNetwork(ssid: "Gast", signal: 31, secured: false),
    ]
    /// Ends the BLE link a moment into the next `connect`, as a wifi switch on the shared radio may.
    public var dropLinkDuringConnect = false
    public var malformNextRequest = false
    public var notifies = true
    /// An agent from before management: its GATT app has only the setup service.
    public var hasManagement = true
    public private(set) var accepted: [ManageRequest] = []
    public private(set) var job: ManageJob?

    var notify: ((Data) -> Void)?
    var dropLink: (() -> Void)?
    var speed: Double = 1

    private final class Session {
        let key: SymmetricKey
        let np: Data
        var lastRequest: UInt32 = 0
        var responseCtr: UInt32 = 0
        var latest = Data()

        init(key: SymmetricKey, np: Data) {
            self.key = key
            self.np = np
        }
    }

    private var session: Session?
    private var jobSession: Session?
    private var tasks: [Task<Void, Never>] = []

    public init(secret: String?, rssi: Int = -60, device: String = "pi-10000000a1b2c3d4",
                name: String? = "Kantine links") {
        self.secret = secret
        self.rssi = rssi
        self.device = device
        self.name = name
    }

    var latestResponse: Data? { session?.latest }

    /// The connection went away or the session idled out (300 s on the Pi).
    public func endSession() {
        session = nil
    }

    func stop() {
        tasks.forEach { $0.cancel() }
        tasks.removeAll()
    }

    func sessionWrite(_ na: Data) throws {
        guard let secret else { throw ManageLinkError.rejected }
        guard na.count == ManageProtocol.nonceLength else { throw ManageLinkError.malformed }
        let np = ManageCrypto.randomNonce()
        session = Session(key: ManageCrypto.sessionKey(secret: secret, na: na, np: np), np: np)
    }

    func sessionRead() throws -> Data {
        guard let session else { throw ManageLinkError.rejected }
        return Data([version]) + session.np
    }

    func requestWrite(_ frame: Data) throws {
        guard secret != nil else { throw ManageLinkError.rejected }
        if malformNextRequest {
            malformNextRequest = false
            throw ManageLinkError.malformed
        }
        guard let s = session else { throw ManageLinkError.rejected }
        let ctr: UInt32, plain: Data
        do {
            (ctr, plain) = try ManageCrypto.open(frame, key: s.key, direction: .request)
        } catch FrameError.badLength {
            throw ManageLinkError.malformed
        } catch {
            throw ManageLinkError.rejected
        }
        guard ctr > s.lastRequest else { throw ManageLinkError.rejected }
        s.lastRequest = ctr
        handle(s, re: Int(ctr), plain)
    }

    private func handle(_ s: Session, re: Int, _ plain: Data) {
        guard let req = (try? JSONSerialization.jsonObject(with: plain)) as? [String: Any],
              let op = req["op"] as? String else {
            return fail(s, re: re, op: nil, error: "bad_request", msg: "Ongeldig verzoek.")
        }
        guard ops.contains(op), let request = ManageRequest(json: plain) else {
            return fail(s, re: re, op: op, error: "unknown_op", msg: nil)
        }
        accepted.append(request)
        switch request {
        case .status:
            respond(s, ["re": re, "op": op, "ok": true, "final": true, "v": 1, "ops": ops, "device": device,
                        "name": name ?? NSNull(), "agent_version": agentVersion, "state": screenState, "paired": paired,
                        "online": online ?? NSNull(), "network": network ?? NSNull(), "ssid": ssid ?? NSNull(),
                        "signal": signal ?? NSNull(), "ip": ip ?? NSNull(), "job": jobJSON])
        case .scan:
            guard job?.state != "busy" else { return busy(s, re: re, op: op) }
            start(s, re: re, ManageJob(op: op, state: "busy"), msg: "Netwerken zoeken…")
            later(2) { pi in
                pi.finish(re: re, ManageJob(op: op, state: "ok"),
                          ["ok": true, "networks": pi.networks.map { [$0.ssid, $0.signal, $0.secured ? 1 : 0] }])
            }
        case let .connect(target, password, _):
            guard job?.state != "busy" else { return busy(s, re: re, op: op) }
            if let problem = Validation.ssid(target) ?? Validation.password(password, secured: false) {
                return fail(s, re: re, op: op, error: "bad_request", msg: problem)
            }
            let old = ssid
            start(s, re: re, ManageJob(op: op, state: "busy", ssid: target), msg: "Verbinden met \(target)…")
            if dropLinkDuringConnect {
                dropLinkDuringConnect = false
                later(1) { pi in
                    pi.session = nil
                    pi.dropLink?()
                }
            }
            later(3) { pi in
                if password == Self.wrongPassword {
                    let msg = "Verbinden met \(target) mislukt: Verkeerd wachtwoord?" + (old.map { " Terug naar \($0)." } ?? "")
                    pi.finish(re: re, ManageJob(op: op, state: "failed", ssid: target, msg: msg),
                              ["ok": false, "error": "failed", "msg": msg, "ssid": old ?? NSNull()])
                } else {
                    (pi.ssid, pi.signal, pi.ip, pi.online, pi.network) = (target, 74, "192.168.1.57", true, "wifi")
                    pi.finish(re: re, ManageJob(op: op, state: "ok", ssid: target),
                              ["ok": true, "msg": NSNull(), "ssid": target, "ip": "192.168.1.57", "online": true])
                }
            }
        }
    }

    private var jobJSON: Any {
        guard let job else { return NSNull() }
        return ["op": job.op, "state": job.state, "ssid": job.ssid ?? NSNull(), "msg": job.msg ?? NSNull()] as [String: Any]
    }

    private func start(_ s: Session, re: Int, _ new: ManageJob, msg: String) {
        job = new
        jobSession = s
        respond(s, ["re": re, "op": new.op, "final": false, "state": "busy", "msg": msg])
    }

    private func finish(re: Int, _ done: ManageJob, _ fields: [String: Any]) {
        job = done
        guard let s = session, s === jobSession else { return }
        respond(s, fields.merging(["re": re, "op": done.op, "final": true]) { a, _ in a })
    }

    private func busy(_ s: Session, re: Int, op: String) {
        fail(s, re: re, op: op, error: "busy", msg: "De speler is nog bezig met een vorige opdracht.")
    }

    private func fail(_ s: Session, re: Int, op: String?, error: String, msg: String?) {
        respond(s, ["re": re, "op": op ?? NSNull(), "ok": false, "final": true, "error": error, "msg": msg ?? NSNull()])
    }

    private func respond(_ s: Session, _ object: [String: Any]) {
        s.responseCtr += 1
        guard let json = try? JSONSerialization.data(withJSONObject: object),
              let frame = try? ManageCrypto.seal(json, key: s.key, direction: .response, ctr: s.responseCtr)
        else { return }
        s.latest = frame
        if notifies { notify?(frame) }
    }

    private func later(_ seconds: Double, _ action: @escaping @MainActor (MockManagePi) -> Void) {
        let delay = UInt64(seconds / speed * 1_000_000_000)
        tasks.append(Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: delay)
            guard let self, !Task.isCancelled else { return }
            action(self)
        })
    }
}

/// Simulates the BLE link to one or more Pis with the same `ZZ-<d>` (the Simulator has no Bluetooth).
@MainActor
public final class MockManageTransport: ManageTransport {
    public var onEvent: ((ManageTransportEvent) -> Void)?
    public let pis: [MockManagePi]
    public private(set) var connects: [UUID] = []
    public private(set) var requestFrames: [Data] = []
    public private(set) var responseReads = 0

    private var current: MockManagePi?
    private var stopped = false
    private let speed: Double

    public init(pis: [MockManagePi], speed: Double = 1) {
        self.pis = pis
        self.speed = speed
        for pi in pis { pi.speed = speed }
    }

    /// One screen that accepts the link's secret, for the Simulator and the `DEMO` sticker.
    public static func demo(link: ManageLink, speed: Double = 1) -> MockManageTransport {
        MockManageTransport(pis: [MockManagePi(secret: link.secret)], speed: speed)
    }

    public func candidates(localName: String) async throws -> [ManageCandidate] {
        stopped = false
        onEvent?(.searching)
        try await pause(1.2)
        try await pause(2)
        return pis.sorted { $0.rssi > $1.rssi }.map { ManageCandidate(id: $0.id, rssi: $0.rssi) }
    }

    public func connect(_ candidate: ManageCandidate) async throws {
        try await pause(0.4)
        guard let pi = pis.first(where: { $0.id == candidate.id }) else { throw ManageLinkError.disconnected }
        guard pi.hasManagement else { throw ManageLinkError.notManagement }
        connects.append(pi.id)
        current = pi
        pi.notify = { [weak self, weak pi] frame in
            guard let self, self.current === pi else { return }
            self.onEvent?(.response(frame, read: false))
        }
        pi.dropLink = { [weak self, weak pi] in
            guard let self, self.current === pi else { return }
            self.current = nil
            self.onEvent?(.disconnected)
        }
    }

    public func writeSession(_ value: Data) async throws {
        try await pause(0.05)
        try connected().sessionWrite(value)
    }

    public func readSession() async throws -> Data {
        try await pause(0.05)
        return try connected().sessionRead()
    }

    public func writeRequest(_ frame: Data) async throws {
        try await pause(0.05)
        requestFrames.append(frame)
        try connected().requestWrite(frame)
    }

    public func readResponse() {
        guard let pi = current else { return }
        responseReads += 1
        Task { @MainActor [weak self] in
            try? await self?.pause(0.05)
            guard let self, self.current === pi, let frame = pi.latestResponse else { return }
            self.onEvent?(.response(frame, read: true))
        }
    }

    public func disconnect() {
        current?.endSession()
        current = nil
    }

    public func stop() {
        stopped = true
        disconnect()
        pis.forEach { $0.stop() }
    }

    private func connected() throws -> MockManagePi {
        guard let current else { throw ManageLinkError.disconnected }
        return current
    }

    private func pause(_ seconds: Double) async throws {
        try await Task.sleep(nanoseconds: UInt64(seconds / speed * 1_000_000_000))
        if stopped { throw CancellationError() }
    }
}
