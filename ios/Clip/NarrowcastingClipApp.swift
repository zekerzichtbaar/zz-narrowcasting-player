import SwiftUI
import WifiSetupKit
import WifiSetupUI

@main
struct NarrowcastingClipApp: App {
    @State private var link: ScreenLink? = SetupLaunch.initialLink
    @State private var unknownLink = false

    var body: some Scene {
        WindowGroup {
            Group {
                if let link {
                    ScreenLinkView(link: link)
                } else {
                    ScanPromptView(message: unknownLink
                        ? "Deze link hoort niet bij een scherm. Scan de QR-code die het scherm bij \"Wifi instellen\" toont, of de QR-sticker op de speler."
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
        if let new = ScreenLink(url: url) {
            link = new
            unknownLink = false
        } else {
            unknownLink = link == nil
        }
    }
}
