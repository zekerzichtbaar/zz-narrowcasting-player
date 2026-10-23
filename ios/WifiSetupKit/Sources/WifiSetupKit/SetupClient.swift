import Foundation
import Observation

/// The setup flow for one screen: turns transport events into what the UI shows.
@MainActor
@Observable
public final class SetupClient {
    public enum Phase: Equatable {
        case bluetooth(BluetoothProblem)
        case searching(slow: Bool)
        case connecting
        case choosing
        case working(String)
        case failed(String)
        case expired
        case online(ssid: String?, code: String?, url: URL?)
        case paired(ssid: String?)

        public var isFinished: Bool {
            switch self {
            case .online, .paired: true
            default: false
            }
        }
    }

    public static let expiredMessage = "QR-code verlopen, scan de code op het scherm opnieuw."

    public private(set) var phase: Phase = .searching(slow: false)
    public private(set) var networks: [WifiNetwork] = []
    public private(set) var isScanning = false
    /// A problem from before this session (e.g. a failed attempt via the hotspot), shown above the list.
    public private(set) var notice: String?
    public let link: SetupLink

    @ObservationIgnored private let transport: SetupTransport
    @ObservationIgnored private var status: SetupStatus?
    @ObservationIgnored private var pending: Pending?
    @ObservationIgnored private var dismissedSeq: Int?
    @ObservationIgnored private var timeout: Task<Void, Never>?
    @ObservationIgnored private let commandTimeout: Duration

    private enum Pending {
        case connect(sentAt: Int)
        case scan(scanID: Int)
    }

    public init(link: SetupLink, transport: SetupTransport, commandTimeout: Duration = .seconds(75)) {
        self.link = link
        self.transport = transport
        self.commandTimeout = commandTimeout
        transport.onEvent = { [weak self] event in self?.handle(event) }
    }

    public func start() {
        transport.start(link: link)
    }

    public func stop() {
        timeout?.cancel()
        transport.stop()
    }

    public func connect(ssid: String, password: String, hidden: Bool) {
        guard send(.connect(ssid: ssid, password: password, hidden: hidden)) else { return }
        pending = .connect(sentAt: status?.seq ?? -1)
        notice = nil
        phase = .working("Verbinden met \(ssid)…")
        armTimeout()
    }

    public func rescan() {
        guard !isScanning, send(.scan) else { return }
        pending = .scan(scanID: status?.scan ?? 0)
        isScanning = true
        armTimeout()
    }

    /// Back to the network list after a failure.
    public func backToList() {
        dismissedSeq = status?.seq
        notice = nil
        phase = .choosing
    }

    private func send(_ command: Command) -> Bool {
        do {
            transport.send(try FrameCrypto.seal(command, token: link.token))
            return true
        } catch {
            phase = .failed("Versleutelen mislukt: \(error.localizedDescription)")
            return false
        }
    }

    private func armTimeout() {
        timeout?.cancel()
        timeout = Task { [weak self, commandTimeout] in
            try? await Task.sleep(for: commandTimeout)
            guard !Task.isCancelled else { return }
            self?.commandTimedOut()
        }
    }

    private func commandTimedOut() {
        switch pending {
        case .connect:
            pending = nil
            phase = .failed("Het scherm reageert niet. Probeer het opnieuw.")
        case .scan:
            pending = nil
            isScanning = false
        case nil:
            break
        }
    }

    func handle(_ event: TransportEvent) {
        switch event {
        case let .bluetooth(problem):
            if !phase.isFinished { phase = .bluetooth(problem) }
        case .searching:
            if !phase.isFinished, phase != .expired { phase = .searching(slow: false) }
        case .notFoundYet:
            if case .searching = phase { phase = .searching(slow: true) }
        case .connecting:
            status = nil
            if !phase.isFinished { phase = .connecting }
        case .connected:
            break
        case let .networks(list):
            networks = list
        case let .status(new):
            apply(new)
        case .writeExpired:
            pending = nil
            isScanning = false
            phase = .expired
        case let .writeFailed(message):
            pending = nil
            isScanning = false
            phase = .failed(message)
        case .disconnected:
            if !phase.isFinished && phase != .expired {
                pending = nil
                isScanning = false
            }
        }
    }

    private func apply(_ new: SetupStatus) {
        if let old = status, new.seq < old.seq { return }
        status = new
        switch new.state {
        case .online:
            finish(.online(ssid: new.ssid, code: new.code, url: new.url))
            return
        case .paired:
            finish(.paired(ssid: new.ssid))
            return
        default:
            break
        }
        if phase == .expired { return }

        switch pending {
        case let .connect(sentAt):
            switch new.state {
            case .busy:
                phase = .working(new.msg ?? "Bezig…")
            case .failed where new.seq > sentAt:
                pending = nil
                timeout?.cancel()
                phase = .failed(new.msg ?? "Verbinden mislukt.")
            default:
                // The Pi starts the job a moment after the write, and reads "ready" while it checks
                // the CMS after joining: wait for online/failed (or the timeout).
                break
            }
            return
        case let .scan(scanID):
            if new.state == .ready && new.scan != scanID {
                pending = nil
                isScanning = false
                timeout?.cancel()
            } else if new.state == .failed {
                pending = nil
                isScanning = false
                timeout?.cancel()
                notice = new.msg
            }
            if !isFirstLook { return }
        case nil:
            break
        }

        switch new.state {
        case .busy:
            phase = .working(new.msg ?? "Bezig…")
        case .failed:
            notice = new.seq == dismissedSeq ? nil : new.msg
            phase = .choosing
        default:
            phase = .choosing
        }
    }

    private var isFirstLook: Bool {
        switch phase {
        case .searching, .connecting, .bluetooth: true
        default: false
        }
    }

    private func finish(_ done: Phase) {
        pending = nil
        isScanning = false
        timeout?.cancel()
        transport.autoReconnect = false
        phase = done
    }
}
