import SwiftUI
import WifiSetupKit
import WifiSetupUI

@main
struct NarrowcastingClipApp: App {
    @State private var link: SetupLink? = SetupLaunch.isMock ? SetupLink(url: SetupLaunch.mockURL) : nil
    @State private var unknownLink = false

    var body: some Scene {
        WindowGroup {
            Group {
                if let link {
                    SetupScreen(link: link)
                } else {
                    ScanPromptView(message: unknownLink
                        ? "Deze link hoort niet bij een scherm. Scan de QR-code die het scherm bij \"Wifi instellen\" toont."
                        : "Open de Camera-app en richt hem op de QR-code die het scherm toont.")
                }
            }
            .onContinueUserActivity(NSUserActivityTypeBrowsingWeb) { activity in
                open(activity.webpageURL)
            }
        }
    }

    private func open(_ url: URL?) {
        guard let url else { return }
        if let new = SetupLink(url: url) {
            link = new
            unknownLink = false
        } else {
            unknownLink = link == nil
        }
    }
}
