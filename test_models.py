import os
import anthropic
import config

if __name__ == "__main__":
    api_key = getattr(config, "ANTHROPIC_API_KEY", "")
    if not api_key:
        print("❌ Error: ANTHROPIC_API_KEY is empty in config/env!")
        exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    print("🔍 Querying Anthropic Models API for your active key...")
    try:
        models_page = client.models.list()
        # Handle both list type and iterator/page
        models_list = list(models_page)
        print(f"✅ Successfully retrieved {len(models_list)} models from API.")
        print("📋 Available Models:")
        for m in models_list:
            print(f"  - {m.id} (Created: {getattr(m, 'created_at', 'N/A')})")
    except Exception as e:
        print(f"❌ Failed to list models: {e}")
        models_list = []

    models_to_test = [
        "claude-3-5-sonnet-20241022",
        "claude-3-5-haiku-20241022",
    ]

    # Add any discovered models that are not in our list
    discovered_ids = [m.id for m in models_list]
    for d_id in discovered_ids:
        if d_id not in models_to_test:
            models_to_test.append(d_id)

    print("\n🔍 Testing Message Creation on Models...")
    for model in models_to_test:
        try:
            response = client.messages.create(
                model=model,
                max_tokens=10,
                messages=[{"role": "user", "content": "ping"}]
            )
            print(f"✅ SUCCESS: Access granted and message created on '{model}'")
        except anthropic.NotFoundError:
            print(f"❌ 404 NOT FOUND: No access or retired model '{model}'")
        except Exception as e:
            print(f"⚠️ ERROR for '{model}': {e}")


