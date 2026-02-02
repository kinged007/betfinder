
import httpx
import asyncio
import sys

async def verify():
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("http://127.0.0.1:8000/health")
            print(f"Status Code: {response.status_code}")
            print(f"Response: {response.json()}")
            
            if response.status_code == 200:
                print("Verification SUCCESS")
                sys.exit(0)
            else:
                print("Verification FAILED")
                sys.exit(1)
    except Exception as e:
        print(f"Verification ERROR: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(verify())
