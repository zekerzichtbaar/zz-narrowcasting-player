import AVFoundation
import SwiftUI
import VisionKit
import WifiSetupKit
import WifiSetupUI

@main
struct NarrowcastingApp: App {
    @State private var link: ScreenLink? = SetupLaunch.initialLink

    var body: some Scene {
        WindowGroup {
            Group {
                if let link {
                    ScreenLinkView(link: link, rescanQR: { self.link = nil }, close: { self.link = nil })
                } else {
                    HomeView { self.link = $0 }
                }
            }
            .onContinueUserActivity(NSUserActivityTypeBrowsingWeb) { activity in
                if let url = activity.webpageURL, let new = ScreenLink(url: url) { link = new }
            }
            .onOpenURL { url in
                if let new = ScreenLink(url: url) { link = new }
            }
        }
    }
}

private struct HomeView: View {
    let found: (ScreenLink) -> Void
    @State private var scanning = false
    @State private var typing = false
    @State private var typed = ""
    @State private var cameraDenied = false

    private var canScan: Bool {
        DataScannerViewController.isSupported
    }

    // isAvailable stays false until the camera is allowed, so ask here instead of hiding the button.
    private func openScanner() async {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            scanning = true
        case .notDetermined:
            if await AVCaptureDevice.requestAccess(for: .video) {
                scanning = true
            } else {
                cameraDenied = true
            }
        default:
            cameraDenied = true
        }
    }

    var body: some View {
        ScanPromptView(message: "Scan de QR-code die het scherm bij \"Wifi instellen\" toont, of de QR-sticker op de speler. Daarna stel je de wifi van het scherm in via Bluetooth.") {
            VStack(spacing: 12) {
                if canScan {
                    Button("Scan de QR-code") { Task { await openScanner() } }
                        .setupPrimaryButton()
                        .accessibilityIdentifier("button.scan")
                }
                Button("Link plakken of typen") { typing = true }
                    .accessibilityIdentifier("button.typeLink")
            }
        }
        .fullScreenCover(isPresented: $scanning) {
            QRScannerView { link in
                scanning = false
                found(link)
            } cancel: {
                scanning = false
            }
            .ignoresSafeArea()
        }
        .alert("Link van het scherm", isPresented: $typing) {
            TextField("https://app.zekerzichtbaar.tv/setup?…", text: $typed)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            Button("Openen") {
                if let link = ScreenLink(string: typed) { found(link) }
            }
            Button("Annuleer", role: .cancel) {}
        } message: {
            Text("Plak de link uit de QR-code op het scherm of de sticker.")
        }
        .alert("Geen toegang tot de camera", isPresented: $cameraDenied) {
            Button("Instellingen") {
                if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
            }
            Button("Annuleer", role: .cancel) {}
        } message: {
            Text("Sta de camera toe in Instellingen om de QR-code op het scherm te scannen.")
        }
    }
}

private struct QRScannerView: UIViewControllerRepresentable {
    let found: (ScreenLink) -> Void
    let cancel: () -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(found: found)
    }

    func makeUIViewController(context: Context) -> UINavigationController {
        let scanner = DataScannerViewController(recognizedDataTypes: [.barcode(symbologies: [.qr])],
                                                qualityLevel: .balanced, isHighlightingEnabled: true)
        scanner.delegate = context.coordinator
        scanner.navigationItem.title = "Scan de QR-code"
        scanner.navigationItem.leftBarButtonItem = UIBarButtonItem(
            systemItem: .cancel, primaryAction: UIAction { _ in cancel() })
        let nav = ScannerNavigationController(rootViewController: scanner)
        nav.overrideUserInterfaceStyle = .dark
        return nav
    }

    func updateUIViewController(_ nav: UINavigationController, context: Context) {}

    final class ScannerNavigationController: UINavigationController {
        override func viewDidAppear(_ animated: Bool) {
            super.viewDidAppear(animated)
            guard let scanner = viewControllers.first as? DataScannerViewController, !scanner.isScanning else { return }
            try? scanner.startScanning()
        }
    }

    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        let found: (ScreenLink) -> Void
        private var done = false

        init(found: @escaping (ScreenLink) -> Void) {
            self.found = found
        }

        func dataScanner(_ dataScanner: DataScannerViewController, didAdd addedItems: [RecognizedItem],
                         allItems: [RecognizedItem]) {
            for case let .barcode(code) in addedItems {
                guard !done, let text = code.payloadStringValue, let link = ScreenLink(string: text) else { continue }
                done = true
                dataScanner.stopScanning()
                found(link)
            }
        }
    }
}
