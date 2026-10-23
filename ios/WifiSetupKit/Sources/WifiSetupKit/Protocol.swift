import CryptoKit
import Foundation

public enum SetupProtocol {
    public static let version = 1
    public static let serviceUUID = "8edd2610-ddcd-4435-a57e-98a6b4dd342f"
    public static let statusUUID = "8edd2611-ddcd-4435-a57e-98a6b4dd342f"
    public static let networksUUID = "8edd2612-ddcd-4435-a57e-98a6b4dd342f"
    public static let commandUUID = "8edd2613-ddcd-4435-a57e-98a6b4dd342f"
    public static let maxValueLength = 512

    static let aad = Data("zz-wifi-setup-v1".utf8)
    static let salt = Data("zz-narrowcasting".utf8)
}

/// What the QR on the TV encodes: `<setup_url>?d=<id>&k=<token>`.
public struct SetupLink: Equatable, Sendable {
    public let deviceID: String
    public let token: Data
    public let url: URL

    public var localName: String { "ZZ-" + deviceID }

    public init?(url: URL) {
        guard let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems,
              let d = items.first(where: { $0.name == "d" })?.value?.uppercased(),
              d.count == 4, d.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber) }),
              let k = items.first(where: { $0.name == "k" })?.value,
              let token = Data(base64URLEncoded: k), token.count == 16
        else { return nil }
        self.deviceID = d
        self.token = token
        self.url = url
    }

    public init?(string: String) {
        guard let url = URL(string: string.trimmingCharacters(in: .whitespacesAndNewlines)) else { return nil }
        self.init(url: url)
    }
}

public enum Command: Equatable, Sendable {
    case connect(ssid: String, password: String, hidden: Bool)
    case scan

    private struct Wire: Codable {
        var op: String
        var ssid: String?
        var password: String?
        var hidden: Bool?
    }

    func json() throws -> Data {
        let wire: Wire
        switch self {
        case let .connect(ssid, password, hidden):
            wire = Wire(op: "connect", ssid: ssid, password: password, hidden: hidden)
        case .scan:
            wire = Wire(op: "scan")
        }
        return try JSONEncoder().encode(wire)
    }

    init?(json: Data) {
        guard let wire = try? JSONDecoder().decode(Wire.self, from: json) else { return nil }
        switch wire.op {
        case "connect":
            self = .connect(ssid: wire.ssid ?? "", password: wire.password ?? "", hidden: wire.hidden ?? false)
        case "scan":
            self = .scan
        default:
            return nil
        }
    }
}

public enum FrameError: Error, Equatable {
    case badLength
    case unauthorized
}

/// COMMAND frames: `len (2, big endian) | nonce (12) | ciphertext + tag (16)`, AES-256-GCM.
public enum FrameCrypto {
    public static func key(token: Data) -> SymmetricKey {
        HKDF<SHA256>.deriveKey(inputKeyMaterial: SymmetricKey(data: token), salt: SetupProtocol.salt,
                               info: SetupProtocol.aad, outputByteCount: 32)
    }

    public static func seal(_ command: Command, token: Data, nonce: AES.GCM.Nonce = AES.GCM.Nonce()) throws -> Data {
        let box = try AES.GCM.seal(try command.json(), using: key(token: token), nonce: nonce,
                                   authenticating: SetupProtocol.aad)
        let body = Data(nonce) + box.ciphertext + box.tag
        var frame = Data([UInt8(body.count >> 8), UInt8(body.count & 0xff)])
        frame.append(body)
        return frame
    }

    static func open(_ frame: Data, token: Data) throws -> Data {
        let bytes = [UInt8](frame)
        guard bytes.count >= 2 + 12 + 16, Int(bytes[0]) << 8 | Int(bytes[1]) == bytes.count - 2 else {
            throw FrameError.badLength
        }
        do {
            let box = try AES.GCM.SealedBox(nonce: AES.GCM.Nonce(data: bytes[2..<14]),
                                            ciphertext: bytes[14..<(bytes.count - 16)],
                                            tag: bytes[(bytes.count - 16)...])
            return try AES.GCM.open(box, using: key(token: token), authenticating: SetupProtocol.aad)
        } catch {
            throw FrameError.unauthorized
        }
    }
}

public struct SetupStatus: Decodable, Equatable, Sendable {
    public enum State: String, Equatable, Sendable {
        case ready, busy, failed, online, paired, unknown
    }

    public var v: Int
    public var seq: Int
    public var state: State
    public var msg: String?
    public var ssid: String?
    public var scan: Int
    public var code: String?
    public var url: URL?

    enum CodingKeys: String, CodingKey { case v, seq, state, msg, ssid, scan, code, url }

    public init(v: Int = SetupProtocol.version, seq: Int, state: State, msg: String? = nil, ssid: String? = nil,
                scan: Int = 0, code: String? = nil, url: URL? = nil) {
        (self.v, self.seq, self.state, self.msg, self.ssid, self.scan, self.code, self.url) =
            (v, seq, state, msg, ssid, scan, code, url)
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        v = try c.decodeIfPresent(Int.self, forKey: .v) ?? SetupProtocol.version
        seq = try c.decode(Int.self, forKey: .seq)
        state = State(rawValue: try c.decode(String.self, forKey: .state)) ?? .unknown
        msg = try c.decodeIfPresent(String.self, forKey: .msg)
        ssid = try c.decodeIfPresent(String.self, forKey: .ssid)
        scan = try c.decodeIfPresent(Int.self, forKey: .scan) ?? 0
        code = try c.decodeIfPresent(String.self, forKey: .code)
        url = (try c.decodeIfPresent(String.self, forKey: .url)).flatMap(URL.init(string:))
    }

    public static func decode(_ data: Data) -> SetupStatus? {
        try? JSONDecoder().decode(SetupStatus.self, from: data)
    }
}

public struct WifiNetwork: Identifiable, Equatable, Sendable {
    public var ssid: String
    public var signal: Int
    public var secured: Bool
    public var id: String { ssid }

    public init(ssid: String, signal: Int, secured: Bool) {
        self.ssid = ssid
        self.signal = signal
        self.secured = secured
    }

    /// NETWORKS value: `[[ssid, signal, secured 0/1], ...]`.
    public static func decodeList(_ data: Data) -> [WifiNetwork]? {
        guard let rows = (try? JSONSerialization.jsonObject(with: data)) as? [[Any]] else { return nil }
        return rows.compactMap { row in
            guard row.count >= 3, let ssid = row[0] as? String, let signal = row[1] as? Int,
                  let secured = row[2] as? Int else { return nil }
            return WifiNetwork(ssid: ssid, signal: signal, secured: secured != 0)
        }
    }
}

public enum Validation {
    public static func password(_ password: String, secured: Bool) -> String? {
        if password.isEmpty {
            return secured ? "Vul het wachtwoord van dit netwerk in." : nil
        }
        return (8...63).contains(password.count) ? nil : "Een wifi-wachtwoord is 8 tot 63 tekens."
    }

    public static func ssid(_ ssid: String) -> String? {
        let trimmed = ssid.trimmingCharacters(in: .whitespaces)
        if trimmed.isEmpty { return "Typ de naam van het netwerk." }
        return trimmed.utf8.count > 32 ? "Netwerknaam is te lang." : nil
    }
}

extension Data {
    init?(base64URLEncoded string: String) {
        var s = string.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        s += String(repeating: "=", count: (4 - s.count % 4) % 4)
        self.init(base64Encoded: s)
    }
}
