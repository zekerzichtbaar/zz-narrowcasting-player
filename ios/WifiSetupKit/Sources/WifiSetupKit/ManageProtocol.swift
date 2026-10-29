import CryptoKit
import Foundation

/// Management over BLE with the sticker secret (docs/ble-management.md).
public enum ManageProtocol {
    public static let version: UInt8 = 1
    public static let serviceUUID = "8edd2620-ddcd-4435-a57e-98a6b4dd342f"
    public static let sessionUUID = "8edd2621-ddcd-4435-a57e-98a6b4dd342f"
    public static let requestUUID = "8edd2622-ddcd-4435-a57e-98a6b4dd342f"
    public static let responseUUID = "8edd2623-ddcd-4435-a57e-98a6b4dd342f"
    public static let nonceLength = 16
    public static let maxValueLength = 512

    static let info = Data("zz-manage-v1".utf8)
    static let salt = Data("zz-narrowcasting".utf8)
    static let overhead = 2 + 4 + 16
}

/// What the sticker encodes: `<setup_url>?d=<id>&s=<secret>`.
public struct ManageLink: Equatable, Sendable, CustomStringConvertible {
    public let deviceID: String
    /// Key material as the exact URL characters; never shown or logged.
    public let secret: String
    public let url: URL

    public var localName: String { "ZZ-" + deviceID }
    public var isDemo: Bool { deviceID == "DEMO" }
    public var description: String { "ManageLink(\(deviceID))" }

    public init?(url: URL) {
        guard let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems,
              let d = items.first(where: { $0.name == "d" })?.value?.uppercased(),
              d.count == 4, d.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber) }),
              let s = items.first(where: { $0.name == "s" })?.value,
              (22...128).contains(s.count),
              s.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-" || $0 == "_") })
        else { return nil }
        self.deviceID = d
        self.secret = s
        self.url = url
    }

    public init?(string: String) {
        guard let url = URL(string: string.trimmingCharacters(in: .whitespacesAndNewlines)) else { return nil }
        self.init(url: url)
    }
}

/// A `/setup` link: `k` is first-time setup and wins, `s` without `k` is management.
public enum ScreenLink: Equatable, Sendable {
    case setup(SetupLink)
    case manage(ManageLink)

    public var deviceID: String {
        switch self {
        case let .setup(link): link.deviceID
        case let .manage(link): link.deviceID
        }
    }

    public init?(url: URL) {
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        if items.contains(where: { $0.name == "k" }) {
            guard let link = SetupLink(url: url) else { return nil }
            self = .setup(link)
        } else if let link = ManageLink(url: url) {
            self = .manage(link)
        } else {
            return nil
        }
    }

    public init?(string: String) {
        guard let url = URL(string: string.trimmingCharacters(in: .whitespacesAndNewlines)) else { return nil }
        self.init(url: url)
    }
}

/// Frames: `len (2) | ctr (4) | ciphertext + tag (16)`, AES-256-GCM under the session key,
/// nonce `dir | 7 zero bytes | ctr`, additional data `"zz-manage-v1" | dir`.
public enum ManageCrypto {
    public enum Direction: UInt8, Sendable {
        case request = 1
        case response = 2
    }

    public static func sessionKey(secret: String, na: Data, np: Data) -> SymmetricKey {
        HKDF<SHA256>.deriveKey(inputKeyMaterial: SymmetricKey(data: Data(secret.utf8)), salt: ManageProtocol.salt,
                               info: ManageProtocol.info + na + np, outputByteCount: 32)
    }

    public static func seal(_ plaintext: Data, key: SymmetricKey, direction: Direction, ctr: UInt32) throws -> Data {
        let box = try AES.GCM.seal(plaintext, using: key, nonce: nonce(direction, ctr),
                                   authenticating: ManageProtocol.info + [direction.rawValue])
        let body = bigEndian(ctr) + box.ciphertext + box.tag
        return Data([UInt8(body.count >> 8), UInt8(body.count & 0xff)]) + body
    }

    /// Throws `FrameError.badLength` for a malformed frame, `.unauthorized` when it is not this key's.
    public static func open(_ frame: Data, key: SymmetricKey, direction: Direction) throws -> (ctr: UInt32, plaintext: Data) {
        let bytes = [UInt8](frame)
        guard bytes.count >= ManageProtocol.overhead, Int(bytes[0]) << 8 | Int(bytes[1]) == bytes.count - 2 else {
            throw FrameError.badLength
        }
        let ctr = bytes[2..<6].reduce(UInt32(0)) { $0 << 8 | UInt32($1) }
        do {
            let box = try AES.GCM.SealedBox(nonce: nonce(direction, ctr), ciphertext: bytes[6..<(bytes.count - 16)],
                                            tag: bytes[(bytes.count - 16)...])
            return (ctr, try AES.GCM.open(box, using: key, authenticating: ManageProtocol.info + [direction.rawValue]))
        } catch {
            throw FrameError.unauthorized
        }
    }

    public static func randomNonce() -> Data {
        SymmetricKey(size: SymmetricKeySize(bitCount: ManageProtocol.nonceLength * 8)).withUnsafeBytes { Data($0) }
    }

    private static func nonce(_ direction: Direction, _ ctr: UInt32) throws -> AES.GCM.Nonce {
        try AES.GCM.Nonce(data: Data([direction.rawValue]) + Data(count: 7) + bigEndian(ctr))
    }

    private static func bigEndian(_ value: UInt32) -> Data {
        Data([UInt8(value >> 24 & 0xff), UInt8(value >> 16 & 0xff), UInt8(value >> 8 & 0xff), UInt8(value & 0xff)])
    }
}

public enum ManageRequest: Equatable, Sendable {
    case status
    case scan
    case connect(ssid: String, password: String, hidden: Bool)

    public var op: String {
        switch self {
        case .status: "status"
        case .scan: "scan"
        case .connect: "connect"
        }
    }

    private struct Wire: Codable {
        var op: String
        var ssid: String?
        var password: String?
        var hidden: Bool?
    }

    public func json() throws -> Data {
        switch self {
        case .status, .scan:
            return try JSONEncoder().encode(Wire(op: op))
        case let .connect(ssid, password, hidden):
            return try JSONEncoder().encode(Wire(op: op, ssid: ssid, password: password, hidden: hidden))
        }
    }

    init?(json: Data) {
        guard let wire = try? JSONDecoder().decode(Wire.self, from: json) else { return nil }
        switch wire.op {
        case "status": self = .status
        case "scan": self = .scan
        case "connect": self = .connect(ssid: wire.ssid ?? "", password: wire.password ?? "", hidden: wire.hidden ?? false)
        default: return nil
        }
    }
}

/// The last `scan`/`connect` a phone started, as `status.job` reports it.
public struct ManageJob: Decodable, Equatable, Sendable {
    public var op: String
    public var state: String
    public var ssid: String?
    public var msg: String?

    public init(op: String, state: String, ssid: String? = nil, msg: String? = nil) {
        (self.op, self.state, self.ssid, self.msg) = (op, state, ssid, msg)
    }
}

/// One decrypted response. `state` is the TV state in a `status` answer and `busy` in progress.
public struct ManageResponse: Decodable, Equatable, Sendable {
    public var re: Int
    public var op: String?
    public var ok: Bool?
    public var final: Bool
    public var state: String?
    public var error: String?
    public var msg: String?
    public var ssid: String?
    public var ip: String?
    public var online: Bool?
    public var networks: [WifiNetwork]?
    public var v: Int?
    public var ops: [String]?
    public var device: String?
    public var name: String?
    public var agentVersion: String?
    public var paired: Bool?
    public var network: String?
    public var signal: Int?
    public var job: ManageJob?

    enum CodingKeys: String, CodingKey {
        case re, op, ok, final, state, error, msg, ssid, ip, online, networks, v, ops, device, name, paired, network,
             signal, job
        case agentVersion = "agent_version"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        func field<T: Decodable>(_ key: CodingKeys) -> T? { (try? c.decodeIfPresent(T.self, forKey: key)) ?? nil }
        re = try c.decode(Int.self, forKey: .re)
        op = field(.op)
        ok = field(.ok)
        final = field(.final) ?? (ok != nil)
        state = field(.state)
        error = field(.error)
        msg = field(.msg)
        ssid = field(.ssid)
        ip = field(.ip)
        online = field(.online)
        networks = (field(.networks) as [Row]?)?.compactMap(\.network)
        v = field(.v)
        ops = field(.ops)
        device = field(.device)
        name = field(.name)
        agentVersion = field(.agentVersion)
        paired = field(.paired)
        network = field(.network)
        signal = field(.signal)
        job = field(.job)
    }

    public static func decode(_ data: Data) -> ManageResponse? {
        try? JSONDecoder().decode(ManageResponse.self, from: data)
    }

    private struct Row: Decodable {
        var network: WifiNetwork?

        init(from decoder: Decoder) throws {
            var u = try decoder.unkeyedContainer()
            guard let ssid = try? u.decode(String.self), let signal = try? u.decode(Int.self),
                  let secured = try? u.decode(Int.self) else { return }
            network = WifiNetwork(ssid: ssid, signal: signal, secured: secured != 0)
        }
    }
}

/// What the status screen shows; every field may be missing on an older or offline Pi.
public struct ManageStatus: Equatable, Sendable {
    public var device: String?
    public var name: String?
    public var agentVersion: String?
    public var screenState: String?
    public var paired: Bool?
    public var online: Bool?
    public var network: String?
    public var ssid: String?
    public var signal: Int?
    public var ip: String?
    public var ops: [String]
    public var job: ManageJob?

    public var canChangeWifi: Bool { ops.contains("scan") && ops.contains("connect") }

    public init(device: String? = nil, name: String? = nil, agentVersion: String? = nil, screenState: String? = nil,
                paired: Bool? = nil, online: Bool? = nil, network: String? = nil, ssid: String? = nil,
                signal: Int? = nil, ip: String? = nil, ops: [String] = [], job: ManageJob? = nil) {
        (self.device, self.name, self.agentVersion, self.screenState, self.paired, self.online) =
            (device, name, agentVersion, screenState, paired, online)
        (self.network, self.ssid, self.signal, self.ip, self.ops, self.job) = (network, ssid, signal, ip, ops, job)
    }

    init(_ r: ManageResponse) {
        self.init(device: r.device, name: r.name, agentVersion: r.agentVersion, screenState: r.state, paired: r.paired,
                  online: r.online, network: r.network, ssid: r.ssid, signal: r.signal, ip: r.ip, ops: r.ops ?? [],
                  job: r.job)
    }
}
