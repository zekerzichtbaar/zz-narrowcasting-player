import Foundation

public struct ManageCandidate: Equatable, Sendable {
    public let id: UUID
    public let rssi: Int

    public init(id: UUID, rssi: Int) {
        self.id = id
        self.rssi = rssi
    }
}

public enum ManageLinkError: Error, Equatable {
    /// ATT 0x08 insufficient authorization.
    case rejected
    /// ATT 0x0e unlikely error: the Pi found the frame malformed.
    case malformed
    case failed(String)
    case disconnected
    case notManagement
    case timeout
}

public enum ManageTransportEvent: Equatable, Sendable {
    case bluetooth(BluetoothProblem)
    case searching
    case notFoundYet
    /// A RESPONSE value; `read` when it answers `readResponse()`, else a notification.
    case response(Data, read: Bool)
    /// The connected Pi went away without `disconnect()`.
    case disconnected
}

/// The management characteristics of one Pi at a time. The client owns the candidate, handshake
/// and retry rules; a transport only moves bytes. Events arrive on the main actor.
@MainActor
public protocol ManageTransport: AnyObject {
    var onEvent: ((ManageTransportEvent) -> Void)? { get set }
    /// Waits for the first advertisement named `localName`, keeps listening ~2 s, strongest first.
    func candidates(localName: String) async throws -> [ManageCandidate]
    /// Connects, finds the management service and subscribes to RESPONSE.
    func connect(_ candidate: ManageCandidate) async throws
    func writeSession(_ value: Data) async throws
    func readSession() async throws -> Data
    func writeRequest(_ frame: Data) async throws
    func readResponse()
    func disconnect()
    func stop()
}
