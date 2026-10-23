import XCTest

/// Walks the App Clip against the simulated Pi (`-mockSetup`): wrong password, retry, online.
/// SCREENSHOT_DIR (passed as TEST_RUNNER_SCREENSHOT_DIR) also saves each step as a PNG.
final class ClipFlowUITests: XCTestCase {
    private var app: XCUIApplication!

    override func setUp() {
        continueAfterFailure = false
        app = XCUIApplication()
        app.launchArguments = ["-mockSetup"]
        app.launch()
    }

    private func element(_ id: String) -> XCUIElement {
        app.descendants(matching: .any)[id]
    }

    private func labelled(_ text: String) -> XCUIElement {
        app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", text)).firstMatch
    }

    private func shot(_ name: String) {
        let png = XCUIScreen.main.screenshot().pngRepresentation
        let attachment = XCTAttachment(data: png, uniformTypeIdentifier: "public.png")
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
        if let dir = ProcessInfo.processInfo.environment["SCREENSHOT_DIR"] {
            try? png.write(to: URL(fileURLWithPath: dir).appendingPathComponent("\(name).png"))
        }
    }

    func testWrongPasswordThenOnline() {
        shot("00-searching")
        let kantine = app.buttons["network.Kantine"]
        XCTAssert(kantine.waitForExistence(timeout: 10))
        shot("01-list")

        kantine.tap()
        let password = app.secureTextFields["field.password"]
        XCTAssert(password.waitForExistence(timeout: 5))
        password.typeText("kort")
        app.buttons["button.connect"].tap()
        XCTAssert(element("label.problem").waitForExistence(timeout: 3))
        shot("02-password-too-short")
        password.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: 4) + "fout1234")
        app.buttons["button.connect"].tap()

        XCTAssert(element("step.working").waitForExistence(timeout: 5))
        shot("03-connecting")
        XCTAssert(app.buttons["button.retry"].waitForExistence(timeout: 10))
        shot("04-failed")
        XCTAssert(labelled("Verkeerd wachtwoord?").exists)

        app.buttons["button.retry"].tap()
        XCTAssert(kantine.waitForExistence(timeout: 5))
        kantine.tap()
        XCTAssert(password.waitForExistence(timeout: 5))
        password.typeText("geheim123\n")

        XCTAssert(element("step.online").waitForExistence(timeout: 10))
        shot("05-online-waiting-for-code")
        XCTAssert(labelled("K7Q-4M2").waitForExistence(timeout: 10))
        XCTAssert(app.buttons["button.pair"].exists)
        shot("06-online-code")
    }

    func testRescanAndOtherNetwork() {
        XCTAssert(app.buttons["network.Kantine"].waitForExistence(timeout: 10))
        app.buttons["button.rescan"].tap()
        sleep(1)
        shot("07-rescanning")
        XCTAssert(app.buttons["network.Buren"].waitForExistence(timeout: 10))
        shot("08-rescanned")

        app.buttons["network.other"].tap()
        let ssid = app.textFields["field.ssid"]
        XCTAssert(ssid.waitForExistence(timeout: 5))
        ssid.typeText("Verstopt")
        shot("09-other-network")
    }
}
