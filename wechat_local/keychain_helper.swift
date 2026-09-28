// A narrowly scoped local Keychain bridge. Secrets travel only through pipes.
import Foundation
import Security
import Darwin

let service = "local.wechat-local-reader.database-keys.v1"
func fail(_ message: String, _ code: Int32 = 2) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8)); exit(code)
}
let args = CommandLine.arguments
guard args.count == 3, ["put", "get", "status", "delete"].contains(args[1]),
      args[2].range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil else {
    fail("Invalid operation or account identifier")
}
let operation = args[1]
let keychainPath = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Keychains/login.keychain-db").path
var keychain: SecKeychain?
let openStatus = SecKeychainOpen(keychainPath, &keychain)
guard openStatus == errSecSuccess, let keychain else { fail("Keychain open status \(openStatus)") }
// An explicit file-based login keychain; no iCloud/data-protection keychain fallback.
let match: [String: Any] = [
    kSecClass as String: kSecClassGenericPassword,
    kSecAttrService as String: service,
    kSecAttrAccount as String: args[2],
    kSecAttrSynchronizable as String: false,
    kSecMatchSearchList as String: [keychain]
]
var status: OSStatus = errSecSuccess
switch operation {
case "put":
    let data = FileHandle.standardInput.readDataToEndOfFile()
    guard !data.isEmpty, data.count <= 262144 else { fail("Invalid payload size") }
    var query = match
    query.removeValue(forKey: kSecMatchSearchList as String)
    query[kSecUseKeychain as String] = keychain
    query[kSecAttrLabel as String] = "微信本地报告：数据库密钥（仅本机）"
    query[kSecValueData as String] = data
    // Do not silently replace an existing account's key set.
    status = SecItemAdd(query as CFDictionary, nil)
case "get", "status":
    var query = match
    query[kSecMatchLimit as String] = kSecMatchLimitOne
    query[operation == "get" ? kSecReturnData as String : kSecReturnAttributes as String] = true
    var result: CFTypeRef?
    status = SecItemCopyMatching(query as CFDictionary, &result)
    if status == errSecSuccess {
        if operation == "get" {
            guard isatty(STDOUT_FILENO) == 0, let data = result as? Data else { fail("Secret output requires a pipe") }
            FileHandle.standardOutput.write(data)
        } else {
            let attrs = result as? [String: Any] ?? [:]
            let sync = (attrs[kSecAttrSynchronizable as String] as? NSNumber)?.boolValue ?? false
            let out = try JSONSerialization.data(withJSONObject: ["exists": true, "synchronizable": sync, "backend": "local-login-keychain"])
            FileHandle.standardOutput.write(out)
        }
    }
case "delete": status = SecItemDelete(match as CFDictionary)
default: fail("Invalid operation")
}
if status == errSecItemNotFound { fail("Project Keychain item not found", 4) }
if status == errSecDuplicateItem { fail("Project Keychain item already exists; refusing overwrite", 5) }
if status != errSecSuccess { fail("Keychain operation status \(status)") }
