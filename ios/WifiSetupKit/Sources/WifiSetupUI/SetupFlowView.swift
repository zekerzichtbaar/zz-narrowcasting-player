#if os(iOS)
import SwiftUI
import UIKit
import WifiSetupKit

/// The whole setup flow for one screen, shared by the app and the App Clip.
public struct SetupFlowView: View {
    let client: SetupClient
    var rescanQR: (() -> Void)?
    var close: (() -> Void)?

    public init(client: SetupClient, rescanQR: (() -> Void)? = nil, close: (() -> Void)? = nil) {
        self.client = client
        self.rescanQR = rescanQR
        self.close = close
    }

    public var body: some View {
        VStack(spacing: 0) {
            TopBar {
                if let close {
                    Button(client.phase.isFinished ? "Klaar" : "Stop", action: close)
                }
            } trailing: {
                if client.phase == .choosing {
                    RescanButton(scanning: client.isScanning) { client.rescan() }
                } else {
                    DeviceLabel(text: "Scherm \(client.link.deviceID)")
                }
            }
            .padding(.horizontal, 20)
            .padding(.top, 8)
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .animation(.smooth(duration: 0.25), value: client.phase)
        }
        .background(Theme.background.ignoresSafeArea())
        .tint(Theme.accent)
        .preferredColorScheme(.dark)
    }

    @ViewBuilder private var content: some View {
        switch client.phase {
        case let .searching(slow):
            SearchingView(slow: slow,
                          title: slow ? "Scherm nog niet gevonden" : "Scherm zoeken…",
                          message: slow
                            ? "Ga dichter bij het scherm staan en controleer of het \"Wifi instellen\" toont. We blijven zoeken."
                            : "Houd je telefoon in de buurt van het scherm.")
                .accessibilityIdentifier("step.searching")
        case .connecting:
            StepView(systemImage: "dot.radiowaves.left.and.right", busy: true,
                     title: "Verbinden met het scherm…", message: nil)
                .accessibilityIdentifier("step.connecting")
        case let .bluetooth(problem):
            BluetoothProblemView(problem: problem)
        case .choosing:
            NetworkListView(client: client)
        case let .working(message):
            StepView(systemImage: "wifi", busy: true, title: message,
                     message: "Dit duurt meestal een halve minuut. Laat deze app open.")
                .accessibilityIdentifier("step.working")
        case let .failed(message):
            StepView(systemImage: "wifi.exclamationmark", tint: Theme.error, title: "Dat lukte niet",
                     message: message) {
                Button("Opnieuw proberen") { client.backToList() }
                    .buttonStyle(PrimaryButtonStyle())
                    .padding(.top, 12)
                    .accessibilityIdentifier("button.retry")
            }
            .accessibilityIdentifier("step.failed")
        case .expired:
            StepView(systemImage: "qrcode.viewfinder", tint: Theme.error, title: "QR-code verlopen",
                     message: SetupClient.expiredMessage) {
                if let rescanQR {
                    Button("QR-code scannen", action: rescanQR)
                        .buttonStyle(PrimaryButtonStyle())
                        .padding(.top, 12)
                }
            }
            .accessibilityIdentifier("step.expired")
        case let .online(ssid, code, url):
            OnlineView(ssid: ssid, code: code, url: url)
        case let .paired(ssid):
            StepView(systemImage: "checkmark.circle.fill", title: "Scherm is gekoppeld",
                     message: ssid.map { "Het scherm is verbonden met \($0) en klaar voor gebruik. Je kunt deze app sluiten." }
                        ?? "Het scherm is online en klaar voor gebruik. Je kunt deze app sluiten.")
                .accessibilityIdentifier("step.paired")
        }
    }
}

private struct BluetoothProblemView: View {
    let problem: BluetoothProblem
    @Environment(\.openURL) private var openURL

    var body: some View {
        switch problem {
        case .off:
            StepView(systemImage: "antenna.radiowaves.left.and.right.slash", tint: Theme.warn,
                     title: "Bluetooth staat uit",
                     message: "Zet Bluetooth aan in het Bedieningspaneel. Het scherm wordt via Bluetooth ingesteld.")
                .accessibilityIdentifier("step.bluetoothOff")
        case .unauthorized:
            StepView(systemImage: "lock.shield", tint: Theme.warn, title: "Geen toegang tot Bluetooth",
                     message: "Sta Bluetooth toe in Instellingen om het scherm te kunnen vinden.") {
                Button("Open Instellingen") {
                    if let url = URL(string: UIApplication.openSettingsURLString) { openURL(url) }
                }
                .buttonStyle(PrimaryButtonStyle())
                .padding(.top, 12)
            }
            .accessibilityIdentifier("step.bluetoothUnauthorized")
        case .unsupported:
            StepView(systemImage: "exclamationmark.triangle", tint: Theme.warn,
                     title: "Bluetooth niet beschikbaar",
                     message: "Deze telefoon ondersteunt geen Bluetooth LE. Gebruik het instelnetwerk dat het scherm toont.")
        }
    }
}

private struct OnlineView: View {
    let ssid: String?
    let code: String?
    let url: URL?
    @Environment(\.openURL) private var openURL

    var body: some View {
        StepView(systemImage: "checkmark", title: "Het scherm is online",
                 message: ssid.map { "Verbonden met \($0)." }) {
            VStack(alignment: .leading, spacing: 14) {
                Kicker(text: "Koppelcode")
                if let code {
                    PairingCodeBoxes(code: code)
                        .accessibilityElement(children: .ignore)
                        .accessibilityLabel(code)
                        .accessibilityIdentifier("label.code")
                } else {
                    HStack(spacing: 10) {
                        Spinner()
                        Text("Code ophalen…").font(Theme.font(16)).foregroundStyle(Theme.muted)
                    }
                    .frame(height: 58)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(22)
            .card()

            VStack(alignment: .leading, spacing: 14) {
                if let url {
                    Button("Koppel dit scherm") { openURL(url) }
                        .buttonStyle(PrimaryButtonStyle())
                        .accessibilityIdentifier("button.pair")
                }
                Text("Koppel het scherm in Narrowcasting met deze code. Het scherm toont dezelfde code.")
                    .font(Theme.font(14))
                    .foregroundStyle(Theme.faint)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityIdentifier("step.online")
    }
}

/// The pairing code in mono boxes, the dash between them (canvas `code`).
struct PairingCodeBoxes: View {
    let code: String

    var body: some View {
        HStack(spacing: 6) {
            ForEach(Array(code.enumerated()), id: \.offset) { _, char in
                if char == "-" {
                    Text("-")
                        .font(Theme.mono(24))
                        .foregroundStyle(Theme.dim)
                        .frame(width: 12)
                } else {
                    Text(String(char))
                        .font(Theme.mono(30, relativeTo: .title))
                        .minimumScaleFactor(0.6)
                        .foregroundStyle(Theme.text)
                        .frame(minWidth: 0, maxWidth: 44, minHeight: 58)
                        .background(Theme.surface, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(Theme.line2, lineWidth: 1))
                }
            }
        }
        .textSelection(.enabled)
    }
}

/// Concentric magenta rings around the z while looking for the screen (canvas "App scherm zoeken").
struct SearchingView: View {
    let slow: Bool
    let title: String
    let message: String
    @State private var pulse = false

    var body: some View {
        GeometryReader { geometry in
            ScrollView {
                VStack(spacing: 40) {
                    ZStack {
                        Circle().strokeBorder(Theme.accent.opacity(0.18), lineWidth: 2)
                            .scaleEffect(pulse ? 1 : 0.92)
                        Circle().strokeBorder(Theme.accent.opacity(0.35), lineWidth: 2).padding(36)
                        Circle().fill(Theme.accent.opacity(0.12)).padding(72)
                        LogoMark(sticker: false).frame(width: 76, height: 64)
                    }
                    .frame(width: 240, height: 240)
                    .accessibilityHidden(true)
                    .animation(slow ? .default : .easeInOut(duration: 1.2).repeatForever(), value: pulse)
                    .onAppear { pulse = !slow }
                    .onChange(of: slow) { pulse = !slow }
                    VStack(spacing: 12) {
                        Text(title)
                            .impactTitle(48)
                            .foregroundStyle(Theme.text)
                            .accessibilityAddTraits(.isHeader)
                        Text(message)
                            .font(Theme.font(17))
                            .lineSpacing(4)
                            .foregroundStyle(Theme.muted)
                    }
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.horizontal, 24)
                .padding(.vertical, 32)
                .frame(maxWidth: .infinity, minHeight: geometry.size.height)
            }
            .scrollBounceBehavior(.basedOnSize)
        }
        .accessibilityElement(children: .contain)
    }
}

/// "Vernieuwen" pill in the header, a spinner while scanning.
struct RescanButton: View {
    let scanning: Bool
    let action: () -> Void

    var body: some View {
        if scanning {
            Spinner().frame(height: 40)
        } else {
            Button(action: action) {
                Label("Vernieuwen", systemImage: "arrow.clockwise")
            }
            .accessibilityIdentifier("button.rescan")
        }
    }
}
#endif
