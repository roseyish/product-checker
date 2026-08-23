import os
import google.generativeai as genai
from dotenv import load_dotenv

# Load the API key from your .env file
load_dotenv()
api_key = os.environ.get("GEMINI_API_KEY")

if not api_key:
    print("No GEMINI_API_KEY found. Check your .env file.")
    exit()

genai.configure(api_key=api_key)

# Use Gemini 2.5 Flash — this is the free-tier model
model = genai.GenerativeModel("gemini-3.6-flash")

response = model.generate_content("Say hello in one short sentence.")

print("SUCCESS. Gemini responded:")
print(response.text)