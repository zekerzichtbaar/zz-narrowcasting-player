import Foundation

public enum BluetoothProblem: Equatable, Sendable {
    case off, unauthorized, unsupported
}

public enum TransportEvent: Equatable, Sendable {
    case bluetooth(BluetoothProblem)
    case searching
    case notFoundYet
    case connecting
    case connected
    case status(SetupStatus)
    case networks([WifiNetwork])
    case writeExpired
    case writeFailed(String)
    case disconnected
}

/// The link to one Pi. Events arrive on the main actor; the BLE and mock transports implement it.
@MainActor
public protocol SetupTransport: AnyObject {
    var onEvent: ((TransportEvent) -> Void)? { get set }
    var autoReconnect: Bool { get set }
    func start(link: SetupLink)
    func send(_ frame: Data)
    func stop()
}
