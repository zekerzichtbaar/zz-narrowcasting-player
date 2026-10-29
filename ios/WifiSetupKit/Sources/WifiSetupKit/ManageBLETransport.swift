import CoreBluetooth
import Foundation

/// CoreBluetooth central for the Pi's management service (docs/ble-management.md).
@MainActor
public final class ManageBLETransport: NSObject, ManageTransport {
    public var onEvent: ((ManageTransportEvent) -> Void)?

    public static let notFoundAfter: TimeInterval = 20
    public static let candidateWindow: TimeInterval = 2
    public static let connectTimeout: TimeInterval = 15

    private let setupServiceID = CBUUID(string: SetupProtocol.serviceUUID)
    private let serviceID = CBUUID(string: ManageProtocol.serviceUUID)
    private let sessionID = CBUUID(string: ManageProtocol.sessionUUID)
    private let requestID = CBUUID(string: ManageProtocol.requestUUID)
    private let responseID = CBUUID(string: ManageProtocol.responseUUID)

    private var central: CBCentralManager?
    private var localName: String?
    private var found: [UUID: (peripheral: CBPeripheral, rssi: Int)] = [:]
    private var scanWaiter: CheckedContinuation<[ManageCandidate], Error>?
    private var windowTimer: Timer?
    private var notFoundTimer: Timer?

    private var peripheral: CBPeripheral?
    private var sessionChar: CBCharacteristic?
    private var requestChar: CBCharacteristic?
    private var responseChar: CBCharacteristic?
    private var connectWaiter: CheckedContinuation<Void, Error>?
    private var connectTimer: Timer?
    private var sessionWriteWaiter: CheckedContinuation<Void, Error>?
    private var sessionReadWaiter: CheckedContinuation<Data, Error>?
    private var requestWriteWaiter: CheckedContinuation<Void, Error>?
    private var responseReadPending = false
    private var closing = false

    public override init() {
        super.init()
    }

    public func candidates(localName: String) async throws -> [ManageCandidate] {
        self.localName = localName
        found.removeAll()
        return try await withCheckedThrowingContinuation { continuation in
            scanWaiter?.resume(throwing: CancellationError())
            scanWaiter = continuation
            if let central {
                stateChanged(central)
            } else {
                central = CBCentralManager(delegate: self, queue: .main)
            }
        }
    }

    public func connect(_ candidate: ManageCandidate) async throws {
        guard let central, central.state == .poweredOn,
              let target = found[candidate.id]?.peripheral
                ?? central.retrievePeripherals(withIdentifiers: [candidate.id]).first
        else { throw ManageLinkError.disconnected }
        resetCharacteristics()
        closing = false
        peripheral = target
        target.delegate = self
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            connectWaiter = continuation
            central.connect(target)
            connectTimer = Timer.scheduledTimer(withTimeInterval: Self.connectTimeout, repeats: false) { [weak self] _ in
                MainActor.assumeIsolated { self?.abortConnect(.timeout) }
            }
        }
    }

    public func writeSession(_ value: Data) async throws {
        let (target, characteristic) = try ready(sessionChar)
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            sessionWriteWaiter?.resume(throwing: ManageLinkError.disconnected)
            sessionWriteWaiter = continuation
            target.writeValue(value, for: characteristic, type: .withResponse)
        }
    }

    public func readSession() async throws -> Data {
        let (target, characteristic) = try ready(sessionChar)
        return try await withCheckedThrowingContinuation { continuation in
            sessionReadWaiter?.resume(throwing: ManageLinkError.disconnected)
            sessionReadWaiter = continuation
            target.readValue(for: characteristic)
        }
    }

    public func writeRequest(_ frame: Data) async throws {
        let (target, characteristic) = try ready(requestChar)
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            requestWriteWaiter?.resume(throwing: ManageLinkError.disconnected)
            requestWriteWaiter = continuation
            target.writeValue(frame, for: characteristic, type: .withResponse)
        }
    }

    public func readResponse() {
        guard let (target, characteristic) = try? ready(responseChar) else { return }
        responseReadPending = true
        target.readValue(for: characteristic)
    }

    public func disconnect() {
        closing = true
        failWaiters(.disconnected)
        if let peripheral { central?.cancelPeripheralConnection(peripheral) }
        peripheral = nil
        resetCharacteristics()
    }

    public func stop() {
        central?.stopScan()
        windowTimer?.invalidate()
        windowTimer = nil
        notFoundTimer?.invalidate()
        scanWaiter?.resume(throwing: CancellationError())
        scanWaiter = nil
        disconnect()
    }

    // MARK: scanning

    private func stateChanged(_ central: CBCentralManager) {
        switch central.state {
        case .poweredOn:
            if scanWaiter != nil { beginScan() }
        case .poweredOff:
            onEvent?(.bluetooth(.off))
        case .unauthorized:
            onEvent?(.bluetooth(.unauthorized))
        case .unsupported:
            onEvent?(.bluetooth(.unsupported))
        default:
            break
        }
    }

    private func beginScan() {
        guard let central, central.state == .poweredOn, windowTimer == nil else { return }
        onEvent?(.searching)
        // Either service: a Pi in its setup window advertises the setup UUID but serves management too.
        central.scanForPeripherals(withServices: [serviceID, setupServiceID],
                                   options: [CBCentralManagerScanOptionAllowDuplicatesKey: true])
        notFoundTimer?.invalidate()
        notFoundTimer = Timer.scheduledTimer(withTimeInterval: Self.notFoundAfter, repeats: false) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self, self.scanWaiter != nil, self.found.isEmpty else { return }
                self.onEvent?(.notFoundYet)
            }
        }
    }

    private func discovered(_ candidate: CBPeripheral, advertisement: [String: Any], rssi: Int) {
        guard scanWaiter != nil, let localName,
              let name = advertisement[CBAdvertisementDataLocalNameKey] as? String,
              name.caseInsensitiveCompare(localName) == .orderedSame
        else { return }
        let strength = rssi == 127 ? -127 : rssi
        found[candidate.identifier] = (candidate, max(found[candidate.identifier]?.rssi ?? -127, strength))
        guard windowTimer == nil else { return }
        windowTimer = Timer.scheduledTimer(withTimeInterval: Self.candidateWindow, repeats: false) { [weak self] _ in
            MainActor.assumeIsolated { self?.finishScan() }
        }
    }

    private func finishScan() {
        central?.stopScan()
        windowTimer = nil
        notFoundTimer?.invalidate()
        let list = found.map { ManageCandidate(id: $0.key, rssi: $0.value.rssi) }.sorted { $0.rssi > $1.rssi }
        scanWaiter?.resume(returning: list)
        scanWaiter = nil
    }

    // MARK: connection

    private func ready(_ characteristic: CBCharacteristic?) throws -> (CBPeripheral, CBCharacteristic) {
        guard let peripheral, peripheral.state == .connected, let characteristic else {
            throw ManageLinkError.disconnected
        }
        return (peripheral, characteristic)
    }

    private func resetCharacteristics() {
        sessionChar = nil
        requestChar = nil
        responseChar = nil
        responseReadPending = false
    }

    private func connected() {
        connectTimer?.invalidate()
        let waiter = connectWaiter
        connectWaiter = nil
        waiter?.resume()
    }

    private func abortConnect(_ error: ManageLinkError) {
        connectTimer?.invalidate()
        let waiter = connectWaiter
        connectWaiter = nil
        closing = true
        if let peripheral { central?.cancelPeripheralConnection(peripheral) }
        peripheral = nil
        resetCharacteristics()
        waiter?.resume(throwing: error)
    }

    private func lost(_ gone: CBPeripheral) {
        guard gone === peripheral else { return }
        let wasLinked = connectWaiter == nil && !closing
        failWaiters(.disconnected)
        peripheral = nil
        resetCharacteristics()
        if wasLinked { onEvent?(.disconnected) }
    }

    private func failWaiters(_ error: ManageLinkError) {
        connectTimer?.invalidate()
        let waiters: [(Error) -> Void] = [
            connectWaiter.map { c in { c.resume(throwing: $0) } },
            sessionWriteWaiter.map { c in { c.resume(throwing: $0) } },
            sessionReadWaiter.map { c in { c.resume(throwing: $0) } },
            requestWriteWaiter.map { c in { c.resume(throwing: $0) } },
        ].compactMap { $0 }
        connectWaiter = nil
        sessionWriteWaiter = nil
        sessionReadWaiter = nil
        requestWriteWaiter = nil
        responseReadPending = false
        waiters.forEach { $0(error) }
    }

    private func linkError(_ error: Error) -> ManageLinkError {
        if let att = error as? CBATTError {
            switch att.code {
            case .insufficientAuthorization: return .rejected
            case .unlikelyError: return .malformed
            default: return .failed(att.localizedDescription)
            }
        }
        if error is CBError { return .disconnected }
        return .failed(error.localizedDescription)
    }

    private func characteristicsFound(_ service: CBService, on target: CBPeripheral) {
        for c in service.characteristics ?? [] {
            switch c.uuid {
            case sessionID: sessionChar = c
            case requestID: requestChar = c
            case responseID: responseChar = c
            default: break
            }
        }
        guard let responseChar, sessionChar != nil, requestChar != nil else {
            return abortConnect(.notManagement)
        }
        target.setNotifyValue(true, for: responseChar)
    }

    private func valueUpdated(_ characteristic: CBCharacteristic, error: Error?) {
        if characteristic.uuid == sessionID {
            let waiter = sessionReadWaiter
            sessionReadWaiter = nil
            if let error {
                waiter?.resume(throwing: linkError(error))
            } else {
                waiter?.resume(returning: characteristic.value ?? Data())
            }
        } else if characteristic.uuid == responseID {
            let read = responseReadPending
            responseReadPending = false
            guard error == nil, let value = characteristic.value else { return }
            onEvent?(.response(value, read: read))
        }
    }

    private func written(_ characteristic: CBCharacteristic, error: Error?) {
        let waiter: CheckedContinuation<Void, Error>?
        switch characteristic.uuid {
        case sessionID:
            waiter = sessionWriteWaiter
            sessionWriteWaiter = nil
        case requestID:
            waiter = requestWriteWaiter
            requestWriteWaiter = nil
        default:
            return
        }
        if let error {
            waiter?.resume(throwing: linkError(error))
        } else {
            waiter?.resume()
        }
    }
}

extension ManageBLETransport: CBCentralManagerDelegate {
    public nonisolated func centralManagerDidUpdateState(_ central: CBCentralManager) {
        MainActor.assumeIsolated { stateChanged(central) }
    }

    public nonisolated func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                                           advertisementData: [String: Any], rssi RSSI: NSNumber) {
        MainActor.assumeIsolated { discovered(peripheral, advertisement: advertisementData, rssi: RSSI.intValue) }
    }

    public nonisolated func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        MainActor.assumeIsolated {
            guard peripheral === self.peripheral else { return }
            peripheral.discoverServices([serviceID])
        }
    }

    public nonisolated func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral,
                                           error: Error?) {
        MainActor.assumeIsolated {
            guard peripheral === self.peripheral else { return }
            abortConnect(.disconnected)
        }
    }

    public nonisolated func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral,
                                           error: Error?) {
        MainActor.assumeIsolated { lost(peripheral) }
    }
}

extension ManageBLETransport: CBPeripheralDelegate {
    public nonisolated func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        MainActor.assumeIsolated {
            guard let service = peripheral.services?.first(where: { $0.uuid == serviceID }) else {
                return abortConnect(.notManagement)
            }
            peripheral.discoverCharacteristics([sessionID, requestID, responseID], for: service)
        }
    }

    public nonisolated func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService,
                                       error: Error?) {
        MainActor.assumeIsolated { characteristicsFound(service, on: peripheral) }
    }

    public nonisolated func peripheral(_ peripheral: CBPeripheral,
                                       didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
        MainActor.assumeIsolated {
            guard characteristic.uuid == responseID, connectWaiter != nil else { return }
            if let error {
                abortConnect(linkError(error))
            } else {
                connected()
            }
        }
    }

    public nonisolated func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic,
                                       error: Error?) {
        MainActor.assumeIsolated { valueUpdated(characteristic, error: error) }
    }

    public nonisolated func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic,
                                       error: Error?) {
        MainActor.assumeIsolated { written(characteristic, error: error) }
    }

    public nonisolated func peripheral(_ peripheral: CBPeripheral, didModifyServices invalidatedServices: [CBService]) {
        MainActor.assumeIsolated {
            guard invalidatedServices.contains(where: { $0.uuid == serviceID }) else { return }
            central?.cancelPeripheralConnection(peripheral)
        }
    }
}
