from src.config import get_config
from openai import OpenAI

c = get_config()
print(f"Provider: {c.api_provider}")
print(f"Model: {c.api_model}")
print(f"Base: {c.api_base_url}")

client = OpenAI(api_key=c.api_key, base_url=c.api_base_url)
r = client.chat.completions.create(
    model=c.api_model,
    messages=[
        {"role": "system", "content": "你是分类专家，只返回JSON。"},
        {"role": "user", "content": "标题: 测试\n正文: 这是一篇关于Agent面试的经验分享\n请返回JSON格式"},
    ],
    temperature=0,
    max_tokens=500,
)

print("\n=== FULL RESPONSE ===")
print(r)
print("\n=== CONTENT ===")
content = r.choices[0].message.content
print(repr(content))
print("\n=== FINISH REASON ===")
print(r.choices[0].finish_reason)
