import Foundation

/// Simulates a Pi for the Simulator (no Bluetooth there). Password "fout1234" fails, anything else
/// gets the screen online with pairing code K7Q-4M2.
@MainActor
public final class MockTransport: SetupTransport {
    public var onEvent: ((TransportEvent) -> Void)?
    public var autoReconnect = true

    public static let wrongPassword = "fout1234"
    public static let pairingURL = URL(string: "https://app.zekerzichtbaar.tv/schermen?code=K7Q-4M2")!

    private var link: SetupLink?
    private var seq = 0
    private var scanID = 1
    private var status = SetupStatus(seq: 0, state: .ready, scan: 1)
    private var tasks: [Task<Void, Never>] = []
    private let speed: Double

    private static let baseNetworks = [
        WifiNetwork(ssid: "Kantine", signal: 82, secured: true),
        WifiNetwork(ssid: "Zeker Zichtbaar", signal: 74, secured: true),
        WifiNetwork(ssid: "Office 5G", signal: 54, secured: true),
        WifiNetwork(ssid: "Gast", signal: 31, secured: false),
    ]

    public init(speed: Double = 1) {
        self.speed = speed
    }

    public func start(link: SetupLink) {
        self.link = link
        later(0) { $0.emit(.searching) }
        later(1.2) { $0.emit(.connecting) }
        later(1.8) { mock in
            mock.emit(.connected)
            mock.publish(.ready)
            mock.emit(.networks(Self.baseNetworks))
        }
    }

    public func send(_ frame: Data) {
        guard let link, let plain = try? FrameCrypto.open(frame, token: link.token) else {
            emit(.writeExpired)
            return
        }
        switch Command(json: plain) {
        case let .connect(ssid, password, _):
            later(0.6) { $0.publish(.busy, msg: "Verbinden met \(ssid)…") }
            later(3) { mock in
                if password == Self.wrongPassword {
                    mock.publish(.failed, msg: "Verbinden met \(ssid) mislukt: Verkeerd wachtwoord?")
                } else {
                    mock.publish(.online, ssid: ssid)
                }
            }
            later(5) { mock in
                guard mock.status.state == .online else { return }
                mock.publish(.online, ssid: ssid, code: "K7Q-4M2", url: Self.pairingURL)
            }
        case .scan:
            later(0.6) { $0.publish(.busy, msg: "Netwerken zoeken…") }
            later(2.5) { mock in
                mock.scanID += 1
                mock.publish(.ready)
                mock.emit(.networks(Self.baseNetworks.map {
                    WifiNetwork(ssid: $0.ssid, signal: max(5, $0.signal - Int.random(in: 0...12)), secured: $0.secured)
                } + [WifiNetwork(ssid: "Buren", signal: 12, secured: true)]))
            }
        case nil:
            emit(.writeFailed("Het scherm begreep de opdracht niet."))
        }
    }

    public func stop() {
        tasks.forEach { $0.cancel() }
        tasks.removeAll()
    }

    private func publish(_ state: SetupStatus.State, msg: String? = nil, ssid: String? = nil, code: String? = nil,
                         url: URL? = nil) {
        seq += 1
        status = SetupStatus(seq: seq, state: state, msg: msg, ssid: ssid, scan: scanID, code: code, url: url)
        emit(.status(status))
    }

    private func emit(_ event: TransportEvent) {
        onEvent?(event)
    }

    private func later(_ seconds: Double, _ action: @escaping @MainActor (MockTransport) -> Void) {
        let delay = UInt64(seconds / speed * 1_000_000_000)
        tasks.append(Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: delay)
            guard let self, !Task.isCancelled else { return }
            action(self)
        })
    }
}
