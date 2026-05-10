import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

def login_to_android_dropper(username, password):
    # Initialize session
    session = requests.Session()
    
    # Configure headers to mimic a real browser
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Referer": "https://android-dropper.pages.dev/",
        "Upgrade-Insecure-Requests": "1",
    }
    
    try:
        # Step 1: Get the login page to extract form details
        print("Fetching login page...")
        base_url = "https://android-dropper.pages.dev/"
        login_page_url = urljoin(base_url, "/login")  # Try common login paths
        response = session.get(login_page_url, headers=headers)
        
        # If first attempt fails, try the base URL
        if response.status_code == 404:
            login_page_url = base_url
            response = session.get(login_page_url, headers=headers)
        
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find the login form
        login_form = soup.find('form', {'id': 'login-form'}) or \
                    soup.find('form', {'class': 'login-form'}) or \
                    soup.find('form')
        
        if not login_form:
            print("Error: Could not find login form on the page")
            return None
        
        # Get form action URL (where to submit)
        form_action = login_form.get('action')
        if not form_action:
            form_action = login_page_url  # Default to current page if no action
        elif form_action.startswith('/'):
            form_action = urljoin(base_url, form_action)
        
        # Find all input fields in the form
        input_fields = login_form.find_all('input')
        login_data = {}
        
        for field in input_fields:
            name = field.get('name')
            value = field.get('value', '')
            if name and name.lower() not in ['username', 'password']:
                login_data[name] = value
        
        # Add credentials
        login_data['username'] = username
        login_data['password'] = password
        
        print(f"Form will be submitted to: {form_action}")
        print(f"Form data: { {k: v if k != 'password' else '*****' for k, v in login_data.items()} }")
        
        # Step 2: Submit login form
        print("Attempting login...")
        login_response = session.post(
            form_action,
            data=login_data,
            headers=headers,
            allow_redirects=True
        )
        login_response.raise_for_status()
        
        # Step 3: Check if login was successful
        success_indicators = [
            "Dashboard",
            "Logout",
            "Welcome",
            "My Account",
            username,
        ]
        
        if any(indicator.lower() in login_response.text.lower() for indicator in success_indicators):
            print("Login successful!")
            return session
        else:
            print("Login failed. Page content:")
            print(login_response.text[:500])  # Print first 500 chars for debugging
            return None
            
    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return None

# Example usage
if __name__ == "__main__":
    USERNAME = "admin123"  # Replace with your actual username
    PASSWORD = "admin123"  # Replace with your actual password
    
    authenticated_session = login_to_android_dropper(USERNAME, PASSWORD)
    
    if authenticated_session:
        print("\nFetching protected content...")
        protected_url = "https://android-dropper.pages.dev/main"
        response = authenticated_session.get(protected_url)
        
        if response.status_code == 200:
            print("Successfully accessed protected content!")
            # Process the protected content here
        else:
            print(f"Failed to access protected content. Status code: {response.status_code}")
    else:
        print("Could not establish authenticated session.")