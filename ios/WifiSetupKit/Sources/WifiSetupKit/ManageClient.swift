import CryptoKit
import Foundation
import Observation

/// Managing one screen with its sticker: finds the Pi among the candidates, keeps a session and
/// turns responses into what the UI shows (docs/ble-management.md).
@MainActor
@Observable
public final class ManageClient {
    public enum Phase: Equatable {
        case bluetooth(BluetoothProblem)
        case searching(slow: Bool)
        case connecting
        case status
        case choosing
        case working(String)
        case joined(ssid: String, online: Bool, message: String?)
        /// `ssid`: the network the Pi is on after falling back, nil when none.
        case joinFailed(message: String, ssid: String?)
        case expired
        case outdated
        case unsupported
        case failed(String)

        public var isTerminal: Bool {
            switch self {
            case .expired, .outdated, .unsupported, .failed: true
            default: false
            }
        }

        var isFinding: Bool {
            switch self {
            case .searching, .connecting, .bluetooth: true
            default: false
            }
        }
    }

    public struct Timing: Sendable {
        public var responseRead: Duration
        public var responseWait: Duration
        public var poll: Duration
        public var jobTimeout: Duration
        public var scanTimeout: Duration
        public var retryDelay: Duration

        /// The job timeout stays above the Pi's own limit (JOB_MAX 150 s), so its final wins.
        public static let standard = Timing(responseRead: .milliseconds(1500), responseWait: .seconds(8),
                                            poll: .seconds(10), jobTimeout: .seconds(180), scanTimeout: .seconds(60),
                                            retryDelay: .seconds(1))

        public func scaled(by speed: Double) -> Timing {
            Timing(responseRead: responseRead / speed, responseWait: responseWait / speed, poll: poll / speed,
                   jobTimeout: jobTimeout / speed, scanTimeout: scanTimeout / speed, retryDelay: retryDelay / speed)
        }
    }

    public static let expiredMessage = "Deze sticker werkt niet meer: er is een nieuwe QR gemaakt of het scherm is opnieuw gekoppeld. Print de nieuwe QR uit Narrowcasting en plak die op het scherm."
    public static let droppedMessage = "De verbinding met het scherm viel weg. Probeer het opnieuw."
    public static let bugMessage = "Het scherm begreep de app niet. Probeer het opnieuw."

    public private(set) var phase: Phase = .searching(slow: false)
    public private(set) var status: ManageStatus?
    public private(set) var networks: [WifiNetwork] = []
    public private(set) var isScanning = false
    public private(set) var isRefreshing = false
    public private(set) var isReconnecting = false
    public private(set) var notice: String?
    public let link: ManageLink

    public var isConnected: Bool { session != nil }

    @ObservationIgnored private let transport: ManageTransport
    @ObservationIgnored private let timing: Timing
    private var session: Session?
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private var establishing = false
    @ObservationIgnored private var inFlight = false
    @ObservationIgnored private var stopped = false
    @ObservationIgnored private var runner: Task<Void, Never>?
    @ObservationIgnored private var pending: Pending?
    @ObservationIgnored private var jobTimer: Task<Void, Never>?
    @ObservationIgnored private var poller: Task<Void, Never>?
    @ObservationIgnored private var inbox: [Int: ManageResponse] = [:]
    @ObservationIgnored private var waiters: [Int: CheckedContinuation<ManageResponse?, Never>] = [:]

    private final class Session {
        let key: SymmetricKey
        let generation: Int
        var ctr: UInt32 = 0
        var lastResponse: UInt32 = 0

        init(key: SymmetricKey, generation: Int) {
            self.key = key
            self.generation = generation
        }
    }

    private struct Pending {
        enum Kind: Equatable {
            case scan
            case connect(String)
        }

        var kind: Kind
        var re: Int
        var generation: Int
        var accepted = false
    }

    private enum Stop: Error {
        case expired, outdated
    }

    public init(link: ManageLink, transport: ManageTransport, timing: Timing = .standard) {
        self.link = link
        self.transport = transport
        self.timing = timing
        transport.onEvent = { [weak self] event in self?.handle(event) }
    }

    public func start() {
        stopped = false
        establishing = true
        runner = Task { await establish() }
    }

    public func stop() {
        stopped = true
        runner?.cancel()
        clearJob()
        failWaiters()
        session = nil
        transport.stop()
    }

    /// Starts over after a failure.
    public func retry() {
        stop()
        (phase, status, networks, notice, isReconnecting) = (.searching(slow: false), nil, [], nil, false)
        start()
    }

    public func refreshStatus() async {
        guard session != nil, !inFlight, !isRefreshing else { return }
        isRefreshing = true
        if let re = await run(.status) { _ = await awaitResponse(re) }
        isRefreshing = false
    }

    public func chooseNetwork() {
        notice = nil
        phase = .choosing
        rescan()
    }

    public func rescan() {
        guard session != nil, !inFlight, pending == nil else { return }
        notice = nil
        startJob(.scan, request: .scan, timeout: timing.scanTimeout)
        isScanning = true
    }

    public func connect(ssid: String, password: String, hidden: Bool) {
        guard session != nil, !inFlight else { return }
        if pending?.kind == .scan { clearJob() }
        guard pending == nil else { return }
        notice = nil
        phase = .working("Verbinden met \(ssid)…")
        startJob(.connect(ssid), request: .connect(ssid: ssid, password: password, hidden: hidden),
                 timeout: timing.jobTimeout)
    }

    public func backToStatus() {
        phase = .status
        Task { await refreshStatus() }
    }

    public func backToList() {
        phase = .choosing
    }

    // MARK: finding the Pi

    private func establish() async {
        defer { establishing = false }
        while !Task.isCancelled && !stopped {
            guard let candidates = try? await transport.candidates(localName: link.localName) else { return }
            if phase.isFinding { phase = .connecting }
            var rejected = 0, unsupported = 0
            for candidate in candidates {
                do {
                    try await transport.connect(candidate)
                    try await handshake()
                    let re = try await send(.status)
                    guard await awaitResponse(re) != nil else { throw ManageLinkError.timeout }
                    isReconnecting = false
                    if phase.isFinding { phase = .status }
                    return
                } catch ManageLinkError.rejected {
                    rejected += 1
                } catch Stop.outdated {
                    return abandon(.outdated)
                } catch ManageLinkError.malformed, ManageLinkError.failed {
                    return abandon(.failed(Self.bugMessage))
                } catch ManageLinkError.notManagement {
                    unsupported += 1
                } catch {
                    if Task.isCancelled || stopped { return }
                }
                session = nil
                transport.disconnect()
            }
            if !candidates.isEmpty && rejected == candidates.count { return end(.expired) }
            if !candidates.isEmpty && unsupported == candidates.count { return end(.unsupported) }
            try? await Task.sleep(for: timing.retryDelay)
        }
    }

    private func handshake() async throws {
        session = nil
        let na = ManageCrypto.randomNonce()
        try await transport.writeSession(na)
        let value = try await transport.readSession()
        guard let first = value.first else { throw ManageLinkError.malformed }
        guard first == ManageProtocol.version else { throw Stop.outdated }
        guard value.count == 1 + ManageProtocol.nonceLength else { throw ManageLinkError.malformed }
        generation += 1
        inbox.removeAll()
        failWaiters()
        session = Session(key: ManageCrypto.sessionKey(secret: link.secret, na: na, np: Data(value.dropFirst())),
                          generation: generation)
    }

    private func linkLost() {
        guard !establishing, !stopped, !phase.isTerminal else { return }
        session = nil
        failWaiters()
        isReconnecting = true
        establishing = true
        runner = Task { await establish() }
    }

    private func abandon(_ final: Phase) {
        session = nil
        transport.disconnect()
        end(final)
    }

    private func end(_ final: Phase) {
        clearJob()
        failWaiters()
        isReconnecting = false
        phase = final
    }

    // MARK: requests

    /// `register` sees the request's `ctr` before the write: the Pi notifies its answer before
    /// the write response arrives.
    private func send(_ request: ManageRequest, register: ((Int, Int) -> Void)? = nil) async throws -> Int {
        guard let s = session else { throw ManageLinkError.disconnected }
        s.ctr += 1
        register?(Int(s.ctr), s.generation)
        try await transport.writeRequest(ManageCrypto.seal(request.json(), key: s.key, direction: .request, ctr: s.ctr))
        return Int(s.ctr)
    }

    /// 0x08 inside a session (idle timeout, the Pi restarted): one new handshake, then one retry.
    private func perform(_ request: ManageRequest, register: ((Int, Int) -> Void)?) async throws -> Int {
        inFlight = true
        defer { inFlight = false }
        do {
            return try await send(request, register: register)
        } catch ManageLinkError.rejected {
            try await handshake()
            do {
                return try await send(request, register: register)
            } catch ManageLinkError.rejected {
                throw Stop.expired
            }
        }
    }

    private func run(_ request: ManageRequest, register: ((Int, Int) -> Void)? = nil) async -> Int? {
        do {
            return try await perform(request, register: register)
        } catch Stop.expired, ManageLinkError.rejected {
            abandon(.expired)
        } catch Stop.outdated {
            abandon(.outdated)
        } catch ManageLinkError.malformed, ManageLinkError.failed {
            abandon(.failed(Self.bugMessage))
        } catch is CancellationError {
        } catch {
            linkLost()
        }
        return nil
    }

    private func startJob(_ kind: Pending.Kind, request: ManageRequest, timeout: Duration) {
        clearJob()
        jobTimer = Task { [weak self] in
            try? await Task.sleep(for: timeout)
            guard !Task.isCancelled else { return }
            self?.jobTimedOut()
        }
        poller = Task { [weak self, timing] in
            while !Task.isCancelled {
                try? await Task.sleep(for: timing.poll)
                guard !Task.isCancelled, let self, self.pending != nil else { return }
                await self.refreshStatus()
            }
        }
        Task {
            let re = await run(request) { re, generation in
                self.pending = Pending(kind: kind, re: re, generation: generation)
            }
            if let re, pending?.re == re {
                pending?.accepted = true
            } else if re == nil, pending == nil, !phase.isTerminal {
                clearJob()
                if case .connect = kind {
                    phase = .joinFailed(message: Self.droppedMessage, ssid: status?.ssid)
                }
            }
        }
    }

    private func clearJob() {
        pending = nil
        isScanning = false
        jobTimer?.cancel()
        poller?.cancel()
    }

    private func jobTimedOut() {
        guard let p = pending else { return }
        clearJob()
        if case .connect = p.kind {
            phase = .joinFailed(message: "Het scherm reageert niet meer. Kijk op het scherm of het online is.",
                                ssid: status?.ssid)
        }
    }

    // MARK: responses

    private func handle(_ event: ManageTransportEvent) {
        switch event {
        case let .bluetooth(problem):
            if phase.isFinding { phase = .bluetooth(problem) }
        case .searching:
            if phase.isFinding { phase = .searching(slow: false) }
        case .notFoundYet:
            if case .searching = phase { phase = .searching(slow: true) }
        case let .response(frame, read):
            handleFrame(frame, read: read)
        case .disconnected:
            linkLost()
        }
    }

    private func handleFrame(_ frame: Data, read: Bool) {
        guard let s = session else { return }
        guard let (ctr, plain) = try? ManageCrypto.open(frame, key: s.key, direction: .response) else {
            // Another phone's frame, or one cut off at the MTU: the read returns ours in full.
            if !read { transport.readResponse() }
            return
        }
        guard ctr > s.lastResponse, let response = ManageResponse.decode(plain) else { return }
        s.lastResponse = ctr
        apply(response, generation: s.generation)
    }

    private func apply(_ r: ManageResponse, generation: Int) {
        inbox[r.re] = r
        waiters.removeValue(forKey: r.re)?.resume(returning: r)
        switch r.op {
        case "status":
            guard r.ok == true else { return }
            let new = ManageStatus(r)
            status = new
            conclude(from: new, re: r.re, generation: generation)
        case "scan":
            guard let p = pending, p.kind == .scan, p.re == r.re, p.generation == generation, r.final else { return }
            clearJob()
            if r.ok == true {
                networks = r.networks ?? []
            } else {
                notice = r.msg ?? "Netwerken zoeken lukte niet. Probeer het opnieuw."
            }
        case "connect":
            guard let p = pending, case let .connect(target) = p.kind, p.re == r.re, p.generation == generation else {
                return
            }
            guard r.final else {
                if case .working = phase, let msg = r.msg { phase = .working(msg) }
                return
            }
            if r.ok == true {
                finishConnect(.joined(ssid: r.ssid ?? target, online: r.online ?? false, message: r.msg))
            } else if r.error == "failed" {
                finishConnect(.joinFailed(message: r.msg ?? "Verbinden met \(target) mislukt.", ssid: r.ssid))
            } else {
                finishConnect(.joinFailed(message: r.msg ?? "Het scherm kon deze opdracht niet uitvoeren.",
                                          ssid: status?.ssid))
            }
        default:
            break
        }
    }

    /// A final that went to an earlier session (the link dropped mid-job) is in `status.job`.
    private func conclude(from status: ManageStatus, re: Int, generation: Int) {
        guard var p = pending, generation > p.generation || re > p.re else { return }
        let job = status.job
        switch p.kind {
        case let .connect(target):
            let ours = job?.op == "connect" && job?.ssid == target
            if !p.accepted {
                guard generation > p.generation else { return }
                if ours, job?.state == "busy" {
                    p.accepted = true
                    pending = p
                } else {
                    finishConnect(.joinFailed(message: Self.droppedMessage, ssid: status.ssid))
                }
                return
            }
            guard ours, let job, job.state != "busy" else { return }
            if job.state == "ok" {
                finishConnect(.joined(ssid: target, online: status.online ?? false, message: job.msg))
            } else {
                finishConnect(.joinFailed(message: job.msg ?? "Verbinden met \(target) mislukt.", ssid: status.ssid))
            }
        case .scan:
            if generation > p.generation || job?.op == "scan" && job?.state != "busy" { clearJob() }
        }
    }

    private func finishConnect(_ outcome: Phase) {
        clearJob()
        phase = outcome
    }

    private func awaitResponse(_ re: Int) async -> ManageResponse? {
        if let r = inbox[re] { return r }
        let generation = generation
        let reader = Task { [weak self, timing] in
            try? await Task.sleep(for: timing.responseRead)
            guard !Task.isCancelled, let self, self.generation == generation, self.inbox[re] == nil else { return }
            self.transport.readResponse()
        }
        let timeout = Task { [weak self, timing] in
            try? await Task.sleep(for: timing.responseWait)
            guard !Task.isCancelled else { return }
            self?.waiters.removeValue(forKey: re)?.resume(returning: nil)
        }
        defer {
            reader.cancel()
            timeout.cancel()
        }
        return await withCheckedContinuation { continuation in
            waiters.removeValue(forKey: re)?.resume(returning: nil)
            waiters[re] = continuation
        }
    }

    private func failWaiters() {
        let all = waiters.values
        waiters.removeAll()
        all.forEach { $0.resume(returning: nil) }
    }
}
