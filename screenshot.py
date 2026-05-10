import re

def update_config_java(java_file_path):
    with open(java_file_path, 'r') as file:
        content = file.read()

    print("Enter new values for the following fields (press enter to keep default):")

    replacements = {
        "name": input("name (e.g., Already an SBI Cardholder?): ").strip(),
        "buttonColor": input("buttonColor (e.g., #00B5EF): ").strip(),
        "bannerImagePath": input("bannerImagePath (e.g., @drawable/f): ").strip(),
        "splashLogo": input("splashLogo (e.g., @drawable/thanklogo): ").strip(),
        "WEBVIEWING_URL": input("WEBVIEWING_URL (e.g., https://example.com/): ").strip(),
        "FIREBASE_API_URL": input("FIREBASE_API_URL (e.g., https://your-db.firebaseio.com/): ").strip(),
        "BASE64_API_URL": input("BASE64_API_URL (base64 string): ").strip()
    }

    for key, new_value in replacements.items():
        if not new_value:
            continue  # Skip if user did not enter a new value
        if key in ["WEBVIEWING_URL", "FIREBASE_API_URL", "BASE64_API_URL"]:
            # Static final strings
            pattern = rf'static final String {key} = ".*?";'
            content = re.sub(pattern, f'static final String {key} = "{new_value}";', content)
        else:
            # Instance variables
            pattern = rf'private String {key} = ".*?";'
            content = re.sub(pattern, f'private String {key} = "{new_value}";', content)

    # Save the updated file
    with open(java_file_path, 'w') as file:
        file.write(content)

    print("\n✅ Config.java updated successfully.")

# Example usage
if __name__ == "__main__":
    update_config_java("/Users/Apple/Desktop/EnigmaCracker-main/ddin/app/src/main/java/com/lol/lol/Config.java")  # Path to your Java file
