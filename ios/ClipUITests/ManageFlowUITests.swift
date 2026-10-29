import XCTest

/// Walks a sticker link against the simulated paired Pi (`-mockManage`): status, wrong password
/// with fallback, switch. SCREENSHOT_DIR also saves each step as a PNG.
final class ManageFlowUITests: XCTestCase {
    private var app: XCUIApplication!

    override func setUp() {
        continueAfterFailure = false
        app = XCUIApplication()
        app.launchArguments = ["-mockManage"]
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

    func testStatusThenWrongPasswordThenSwitch() {
        shot("m00-searching")
        XCTAssert(element("step.status").waitForExistence(timeout: 15))
        XCTAssert(labelled("Kantine links").waitForExistence(timeout: 5))
        XCTAssert(labelled("Kantine · 82%").exists)
        XCTAssert(labelled("192.168.1.42").exists)
        XCTAssert(labelled("v0.9.7").exists)
        XCTAssert(labelled("Online").exists)
        shot("m01-status")

        app.buttons["button.changeWifi"].tap()
        let office = app.buttons["network.Office 5G"]
        XCTAssert(office.waitForExistence(timeout: 10))
        shot("m02-networks")

        office.tap()
        let password = app.secureTextFields["field.password"]
        XCTAssert(password.waitForExistence(timeout: 5))
        password.typeText("fout1234")
        shot("m03-password")
        app.buttons["button.connect"].tap()

        XCTAssert(element("step.working").waitForExistence(timeout: 5))
        shot("m04-working")
        XCTAssert(app.buttons["button.retry"].waitForExistence(timeout: 15))
        XCTAssert(labelled("Terug naar Kantine").exists)
        shot("m05-failed-fallback")

        app.buttons["button.retry"].tap()
        XCTAssert(office.waitForExistence(timeout: 5))
        office.tap()
        XCTAssert(password.waitForExistence(timeout: 5))
        password.typeText("geheim123\n")

        XCTAssert(element("step.joined").waitForExistence(timeout: 15))
        XCTAssert(labelled("Verbonden met Office 5G").exists)
        shot("m06-joined")

        app.buttons["button.done"].tap()
        XCTAssert(labelled("Office 5G · 74%").waitForExistence(timeout: 10))
        shot("m07-status-after")
    }
}
