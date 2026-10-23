import CoreBluetooth
import Foundation

/// CoreBluetooth central for the Pi's wifi-setup service (docs/ble-setup.md).
@MainActor
public final class BLETransport: NSObject, SetupTransport {
    public var onEvent: ((TransportEvent) -> Void)?
    public var autoReconnect = true

    public static let notFoundAfter: TimeInterval = 20

    private let serviceID = CBUUID(string: SetupProtocol.serviceUUID)
    private let statusID = CBUUID(string: SetupProtocol.statusUUID)
    private let networksID = CBUUID(string: SetupProtocol.networksUUID)
    private let commandID = CBUUID(string: SetupProtocol.commandUUID)

    private var central: CBCentralManager?
    private var link: SetupLink?
    private var peripheral: CBPeripheral?
    private var statusChar: CBCharacteristic?
    private var networksChar: CBCharacteristic?
    private var commandChar: CBCharacteristic?
    private var lastScanID: Int?
    private var statusReadPending = false
    private var notFoundTimer: Timer?
    private var stopped = false

    public override init() {
        super.init()
    }

    public func start(link: SetupLink) {
        self.link = link
        stopped = false
        if let central {
            stateChanged(central)
        } else {
            central = CBCentralManager(delegate: self, queue: .main)
        }
    }

    public func send(_ frame: Data) {
        guard let peripheral, let commandChar, peripheral.state == .connected else {
            onEvent?(.writeFailed("Geen verbinding met het scherm. Even geduld, er wordt opnieuw verbonden."))
            return
        }
        peripheral.writeValue(frame, for: commandChar, type: .withResponse)
    }

    public func stop() {
        stopped = true
        notFoundTimer?.invalidate()
        central?.stopScan()
        if let peripheral { central?.cancelPeripheralConnection(peripheral) }
    }

    private func stateChanged(_ central: CBCentralManager) {
        switch central.state {
        case .poweredOn:
            if peripheral?.state != .connected { beginScan() }
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
        guard let central, central.state == .poweredOn, link != nil, !stopped else { return }
        resetCharacteristics()
        peripheral = nil
        onEvent?(.searching)
        // Duplicates on: the name can arrive in the scan response after the first advertisement.
        central.scanForPeripherals(withServices: [serviceID],
                                   options: [CBCentralManagerScanOptionAllowDuplicatesKey: true])
        notFoundTimer?.invalidate()
        notFoundTimer = Timer.scheduledTimer(withTimeInterval: Self.notFoundAfter, repeats: false) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self, self.peripheral == nil, !self.stopped else { return }
                self.onEvent?(.notFoundYet)
            }
        }
    }

    private func resetCharacteristics() {
        statusChar = nil
        networksChar = nil
        commandChar = nil
        lastScanID = nil
        statusReadPending = false
    }

    private func discovered(_ found: CBPeripheral, advertisement: [String: Any]) {
        guard peripheral == nil, let link,
              let name = advertisement[CBAdvertisementDataLocalNameKey] as? String,
              name.caseInsensitiveCompare(link.localName) == .orderedSame
        else { return }
        central?.stopScan()
        notFoundTimer?.invalidate()
        peripheral = found
        found.delegate = self
        onEvent?(.connecting)
        central?.connect(found)
    }

    private func lost() {
        resetCharacteristics()
        onEvent?(.disconnected)
        if autoReconnect && !stopped { beginScan() }
    }

    private func handleValue(_ characteristic: CBCharacteristic, error: Error?) {
        guard let peripheral, error == nil, let data = characteristic.value else { return }
        if characteristic.uuid == statusID {
            let wasRead = statusReadPending
            statusReadPending = false
            guard let status = SetupStatus.decode(data) else {
                // A notification cut off at the MTU: the read returns the whole value.
                if !wasRead {
                    statusReadPending = true
                    peripheral.readValue(for: characteristic)
                }
                return
            }
            if status.scan != lastScanID, let networksChar {
                lastScanID = status.scan
                peripheral.readValue(for: networksChar)
            }
            onEvent?(.status(status))
        } else if characteristic.uuid == networksID, let list = WifiNetwork.decodeList(data) {
            onEvent?(.networks(list))
        }
    }

    private func handleWrite(error: Error?) {
        guard let error else { return }
        if let att = error as? CBATTError, att.code == .insufficientAuthorization {
            onEvent?(.writeExpired)
        } else {
            onEvent?(.writeFailed("Het scherm weigerde de opdracht: \(error.localizedDescription)"))
        }
    }
}

extension BLETransport: CBCentralManagerDelegate {
    public nonisolated func centralManagerDidUpdateState(_ central: CBCentralManager) {
        MainActor.assumeIsolated { stateChanged(central) }
    }

    public nonisolated func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                                           advertisementData: [String: Any], rssi RSSI: NSNumber) {
        MainActor.assumeIsolated { discovered(peripheral, advertisement: advertisementData) }
    }

    public nonisolated func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        MainActor.assumeIsolated {
            peripheral.discoverServices([serviceID])
        }
    }

    public nonisolated func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral,
                                           error: Error?) {
        MainActor.assumeIsolated { lost() }
    }

    public nonisolated func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral,
                                           error: Error?) {
        MainActor.assumeIsolated { lost() }
    }
}

extension BLETransport: CBPeripheralDelegate {
    public nonisolated func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        MainActor.assumeIsolated {
            guard let service = peripheral.services?.first(where: { $0.uuid == serviceID }) else {
                central?.cancelPeripheralConnection(peripheral)
                return
            }
            peripheral.discoverCharacteristics([statusID, networksID, commandID], for: service)
        }
    }

    public nonisolated func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService,
                                       error: Error?) {
        MainActor.assumeIsolated {
            for c in service.characteristics ?? [] {
                switch c.uuid {
                case statusID: statusChar = c
                case networksID: networksChar = c
                case commandID: commandChar = c
                default: break
                }
            }
            guard let statusChar, networksChar != nil, commandChar != nil else {
                central?.cancelPeripheralConnection(peripheral)
                return
            }
            peripheral.setNotifyValue(true, for: statusChar)
            statusReadPending = true
            peripheral.readValue(for: statusChar)
            onEvent?(.connected)
        }
    }

    public nonisolated func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic,
                                       error: Error?) {
        MainActor.assumeIsolated { handleValue(characteristic, error: error) }
    }

    public nonisolated func peripheral(_ peripheral: CBPeripheral, didWriteValueFor characteristic: CBCharacteristic,
                                       error: Error?) {
        MainActor.assumeIsolated { handleWrite(error: error) }
    }

    public nonisolated func peripheral(_ peripheral: CBPeripheral, didModifyServices invalidatedServices: [CBService]) {
        MainActor.assumeIsolated {
            guard invalidatedServices.contains(where: { $0.uuid == serviceID }) else { return }
            resetCharacteristics()
            peripheral.discoverServices([serviceID])
        }
    }
}
