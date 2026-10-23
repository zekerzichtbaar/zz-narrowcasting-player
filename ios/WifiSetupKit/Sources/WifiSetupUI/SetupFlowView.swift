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
        NavigationStack {
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Theme.background.ignoresSafeArea())
                .navigationTitle("Scherm \(client.link.deviceID)")
                .navigationBarTitleDisplayMode(.inline)
                .toolbarBackground(Theme.background, for: .navigationBar)
                .toolbar {
                    if let close {
                        ToolbarItem(placement: .cancellationAction) {
                            Button(client.phase.isFinished ? "Klaar" : "Stop", action: close)
                        }
                    }
                }
                .animation(.smooth(duration: 0.25), value: client.phase)
        }
        .tint(Theme.accent)
        .preferredColorScheme(.dark)
    }

    @ViewBuilder private var content: some View {
        switch client.phase {
        case let .searching(slow):
            StepView(systemImage: "antenna.radiowaves.left.and.right", busy: !slow,
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
            StepView(systemImage: "antenna.radiowaves.left.and.right.slash", tint: Theme.error,
                     title: "Bluetooth staat uit",
                     message: "Zet Bluetooth aan in het Bedieningspaneel. Het scherm wordt via Bluetooth ingesteld.")
                .accessibilityIdentifier("step.bluetoothOff")
        case .unauthorized:
            StepView(systemImage: "lock.shield", tint: Theme.error, title: "Geen toegang tot Bluetooth",
                     message: "Sta Bluetooth toe in Instellingen om het scherm te kunnen vinden.") {
                Button("Open Instellingen") {
                    if let url = URL(string: UIApplication.openSettingsURLString) { openURL(url) }
                }
                .buttonStyle(PrimaryButtonStyle())
                .padding(.top, 12)
            }
            .accessibilityIdentifier("step.bluetoothUnauthorized")
        case .unsupported:
            StepView(systemImage: "exclamationmark.triangle", tint: Theme.error,
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
        StepView(systemImage: "checkmark.circle.fill", title: "Het scherm is online",
                 message: ssid.map { "Verbonden met \($0)." }) {
            VStack(spacing: 10) {
                Text("KOPPELCODE")
                    .font(.caption.monospaced().weight(.medium))
                    .tracking(1.5)
                    .foregroundStyle(Theme.muted)
                if let code {
                    Text(code)
                        .font(.system(size: 44, weight: .semibold, design: .monospaced))
                        .foregroundStyle(Theme.text)
                        .textSelection(.enabled)
                        .accessibilityIdentifier("label.code")
                } else {
                    HStack(spacing: 10) {
                        ProgressView()
                        Text("Code ophalen…").foregroundStyle(Theme.muted)
                    }
                    .frame(height: 52)
                }
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 20)
            .background(Theme.card, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
            .padding(.top, 8)

            if let url {
                Button("Koppel dit scherm") { openURL(url) }
                    .buttonStyle(PrimaryButtonStyle())
                    .accessibilityIdentifier("button.pair")
            }
            Text("Koppel het scherm in Narrowcasting met deze code. Het scherm toont dezelfde code.")
                .font(.footnote)
                .multilineTextAlignment(.center)
                .foregroundStyle(Theme.muted)
        }
        .accessibilityIdentifier("step.online")
    }
}
#endif
