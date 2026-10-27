# SmartThings Find NextGen
This is a spiritual successor to https://github.com/Vedeneb/HA-SmartThings-Find as I really wanted that feature.

Viewing smart tags locations on Home Assistant

Each smart tag shows up as a device with its location, battery and last seen time, and buttons to make it ring or to ask it for its current location.

I only have 1 smart tag so I haven't tested it with more than 1, it should work as I dynamically get the list but it might be broken on certain conditions. If you find bugs, feel free to open an issue.

## Installation

### Method 1: HACS (Recommended)

Since this integration is not currently in the default HACS store, you can easily add it as a Custom Repository:

1. Open **HACS** in your Home Assistant dashboard.
2. Click the **three dots** in the top right corner and select **Custom repositories**.
3. Paste the URL of this GitHub repository into the **Repository** box.
4. For **Category**, select **Integration**.
5. Click **Add**.
6. Find the **SmartThings Find NextGen** integration in the list and click **Download**.
7. Restart Home Assistant.

---

### Method 2: Manual Installation

If you prefer not to use HACS, you can install the integration files directly onto your server:

1. Download the latest release source code (or clone this repository).
2. Using an SSH client, Samba, or the File Editor add-on, locate your Home Assistant `config/` directory.
3. Look for a folder named `custom_components`. If it does not exist, create it.
4. Copy the `custom_components/smarttags_nextgen` folder from this repository into your `custom_components/` directory, so that you have `config/custom_components/smarttags_nextgen/`.
5. Restart Home Assistant.

## Setup Instructions

1. Go to the Integrations page.
2. Search "SmartThings Find NextGen".
3. Choose how to connect:

### Option 1: Sign in with Samsung account (sessions renew automatically)

1. Open the Developer Tools of your browser (F12) and switch to the **Console** tab.
2. Open the sign-in link shown by Home Assistant in the same tab and sign in to your Samsung account.
3. At the end, the browser tries to open an address starting with `ms-app://` and fails. Copy that complete address (Chrome and Edge show it in the Console as *Failed to launch 'ms-app://…'*, otherwise look for it in the **Network** tab) and paste it into Home Assistant.

Home Assistant keeps a Samsung account token and uses it to create a new SmartThings Find session whenever the current one expires, so you only need to sign in again after logging out of your Samsung account or changing its security settings.

**Treat this token like your Samsung password:** anyone with access to your Home Assistant configuration (or its backups) can use it to access your Samsung account. The sign-in is based on the reverse-engineered protocol documented by [samsung-re-find](https://github.com/charlesbel/samsung-re-find) and [uTag](https://github.com/KieronQuinn/uTag/wiki/Authentication); Samsung can change or block it at any time.

### Option 2: Enter a JSESSIONID

1. Visit https://smartthingsfind.samsung.com/ and log in with your Samsung account.
2. Open Developer Tools in your browser.
3. Copy the JSESSIONID value. Note to copy the one from smartthingsfind.samsung.com (you might have another from another domain).
4. Enter your JSESSIONID into Home Assistant.

The session expires after a while; Home Assistant then asks you for a new JSESSIONID.

You can switch between both options, or change the region, with **Reconfigure** in the integration's menu. The update interval can be changed in the integration's options.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Contributions

Contributions are welcome! Feel free to open issues or submit pull requests to help improve this integration.

## Support

For support, please create an issue on the GitHub repository.

## Roadmap

- Maybe more comfortable login

## Disclaimer

This is a third-party integration and is not affiliated with or endorsed by Samsung or SmartThings.
