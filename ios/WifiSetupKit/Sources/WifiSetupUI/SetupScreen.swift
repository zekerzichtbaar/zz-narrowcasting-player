#if os(iOS)
import SwiftUI
import WifiSetupKit

/// Launch switches: `-mockSetup` (or env MOCK_SETUP=1) runs the flow against a simulated Pi;
/// `-mockManage` (or env MOCK_MANAGE=1) opens a sticker link against a simulated paired Pi.
public enum SetupLaunch {
    public static let mockURL = URL(string: "https://app.zekerzichtbaar.tv/setup?d=1E2F&k=AAECAwQFBgcICQoLDA0ODw")!
    public static let mockManageURL = URL(string: "https://app.zekerzichtbaar.tv/setup?d=1E2F&s=0123456789abcdefghijklmnopqrstuvwxyzABCDEFG")!

    public static var isMock: Bool {
        ProcessInfo.processInfo.arguments.contains("-mockSetup")
            || ProcessInfo.processInfo.environment["MOCK_SETUP"] == "1"
    }

    public static var isMockManage: Bool {
        ProcessInfo.processInfo.arguments.contains("-mockManage")
            || ProcessInfo.processInfo.environment["MOCK_MANAGE"] == "1"
    }

    /// The link a mock launch opens with, nil for a normal launch.
    public static var initialLink: ScreenLink? {
        if isMockManage { return ScreenLink(url: mockManageURL) }
        return isMock ? ScreenLink(url: mockURL) : nil
    }
}

/// First-time setup for a TV QR, management for a sticker.
public struct ScreenLinkView: View {
    let link: ScreenLink
    var rescanQR: (() -> Void)?
    var close: (() -> Void)?

    public init(link: ScreenLink, rescanQR: (() -> Void)? = nil, close: (() -> Void)? = nil) {
        self.link = link
        self.rescanQR = rescanQR
        self.close = close
    }

    public var body: some View {
        switch link {
        case let .setup(setup):
            SetupScreen(link: setup, rescanQR: rescanQR, close: close)
        case let .manage(manage):
            ManageScreen(link: manage, rescanQR: rescanQR, close: close)
        }
    }
}

/// Owns the SetupClient for a link; a new link (another QR) starts over.
public struct SetupScreen: View {
    let link: SetupLink
    var rescanQR: (() -> Void)?
    var close: (() -> Void)?
    @State private var client: SetupClient?

    public init(link: SetupLink, rescanQR: (() -> Void)? = nil, close: (() -> Void)? = nil) {
        self.link = link
        self.rescanQR = rescanQR
        self.close = close
    }

    public var body: some View {
        Group {
            if let client {
                SetupFlowView(client: client, rescanQR: rescanQR, close: close)
            } else {
                Theme.background.ignoresSafeArea()
            }
        }
        .task(id: link) {
            client?.stop()
            let transport: SetupTransport = SetupLaunch.isMock || link.isDemo ? MockTransport() : BLETransport()
            let new = SetupClient(link: link, transport: transport)
            new.start()
            client = new
        }
        .onDisappear { client?.stop() }
    }
}

/// What the App Clip and the app show before they have a link.
public struct ScanPromptView<Actions: View>: View {
    var message: String
    @ViewBuilder var actions: Actions

    public init(message: String, @ViewBuilder actions: () -> Actions) {
        self.message = message
        self.actions = actions()
    }

    public var body: some View {
        StepView(systemImage: "qrcode.viewfinder", title: "Scan de QR-code op het scherm", message: message) {
            actions.padding(.top, 12)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.background.ignoresSafeArea())
        .preferredColorScheme(.dark)
        .tint(Theme.accent)
    }
}

public extension ScanPromptView where Actions == EmptyView {
    init(message: String) {
        self.init(message: message) { EmptyView() }
    }
}

public extension View {
    /// Primary green button style of the setup flow.
    func setupPrimaryButton() -> some View {
        buttonStyle(PrimaryButtonStyle())
    }
}
#endif
