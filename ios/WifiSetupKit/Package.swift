// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "WifiSetupKit",
    defaultLocalization: "nl",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "WifiSetupKit", targets: ["WifiSetupKit"]),
        .library(name: "WifiSetupUI", targets: ["WifiSetupUI"]),
    ],
    targets: [
        .target(name: "WifiSetupKit"),
        .target(name: "WifiSetupUI", dependencies: ["WifiSetupKit"]),
        .testTarget(name: "WifiSetupKitTests", dependencies: ["WifiSetupKit"]),
    ],
    swiftLanguageModes: [.v5]
)
