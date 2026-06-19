"""
Flight-Monitor - 预测模块
价格变动触发告警时，调用 DeepSeek API 分析趋势，给出购买建议。
历史数据不足时生成本地人性化提示，不浪费 API 调用。
"""

import requests
from datetime import datetime

import config
import database


def get_flight_history(flight_no, route_from, route_to, flight_date):
    """获取指定航班的历史价格数据"""
    return database.get_price_history(flight_no, route_from, route_to, flight_date)


def predict_trend(flight_info):
    """分析航班价格趋势，返回建议文本。

    Args:
        flight_info: dict {
            flight_no, route_from, route_to, flight_date,
            from_name, to_name, airline, price, old_price, new_price,
            change_amount, change_percent, departure_time, arrival_time,
        }

    Returns:
        str: AI 趋势分析文本，失败或未配置返回 None
    """
    if not config.DEEPSEEK_API_KEY:
        return None

    history = get_flight_history(
        flight_info['flight_no'],
        flight_info.get('route_from', ''),
        flight_info.get('route_to', ''),
        flight_info['flight_date'],
    )

    # 距起飞天数
    days_until = None
    try:
        flight_date = datetime.strptime(flight_info['flight_date'], '%Y-%m-%d')
        days_until = (flight_date - datetime.now()).days
    except Exception:
        pass

    # 数据不足 → 本地生成友好提示，不调 API
    if len(history) < 3:
        return _insufficient_data_tip(flight_info, history, days_until)

    # 调 DeepSeek API
    prompt = _build_prompt(flight_info, history, days_until)

    try:
        return _call_deepseek(prompt)
    except Exception as e:
        print(f"   ⚠️ AI 预测失败: {e}")
        return _fallback_tip(flight_info, history, days_until)


# ============================================================
#  内部函数
# ============================================================

def _insufficient_data_tip(flight_info, history, days_until):
    """历史数据不足（< 3 条）时，生成本地人性化提示"""
    record_count = len(history)
    tips = []

    if record_count == 0:
        tips.append(f"📊 这是 {flight_info['flight_no']} 的首次价格记录，再多监控几轮就能分析趋势啦。")
    elif record_count == 1:
        tips.append(f"📊 {flight_info['flight_no']} 仅有 1 条历史记录，数据还太少，继续监控中～")
    else:
        tips.append(f"📊 {flight_info['flight_no']} 仅有 {record_count} 条历史记录，暂不足以判断趋势。")

    if days_until is not None:
        if days_until > 30:
            tips.append(f"距起飞还有 {days_until} 天，时间充裕，可以先关注着。")
        elif days_until > 14:
            tips.append(f"距起飞还有 {days_until} 天，建议持续关注价格变化。")
        elif days_until > 7:
            tips.append(f"距起飞还有 {days_until} 天，进入价格敏感期，多留意。")
        else:
            tips.append(f"⚠️ 距起飞仅 {days_until} 天，时间较紧，建议尽快决策。")

    change = flight_info.get('change_amount', 0)
    if change > 0:
        tips.append(f"当前涨价 ¥{change}，注意不要追高。")
    elif change < 0:
        tips.append(f"当前降价 ¥{abs(change)}，可以把这个价位记下来作为参考。")

    return ' '.join(tips)


def _build_prompt(flight_info, history, days_until):
    """构建发给 DeepSeek 的分析 prompt"""
    prices = [h['price'] for h in history]
    min_price = min(prices)
    max_price = max(prices)
    avg_price = sum(prices) / len(prices)
    current_price = flight_info['price']
    pct_from_min = (current_price - min_price) / min_price * 100 if min_price > 0 else 0

    # 近 7 天趋势
    now = datetime.now()
    recent = [h for h in history
              if (now - datetime.strptime(h['time'], '%Y-%m-%d %H:%M:%S')).days <= 7]
    if len(recent) >= 2:
        if recent[-1]['price'] > recent[0]['price']:
            recent_trend = "上涨"
        elif recent[-1]['price'] < recent[0]['price']:
            recent_trend = "下降"
        else:
            recent_trend = "持平"
    else:
        recent_trend = "数据不足"

    # 最近 20 条历史价格
    timeline_lines = [f"  {h['time'][:10]}  ¥{h['price']}" for h in history[-20:]]
    timeline = "\n".join(timeline_lines)

    return f"""你是一个机票价格分析助手。请根据以下数据，分析航班价格趋势并给出购买建议。

## 航班信息
- 航线: {flight_info.get('from_name', '')} → {flight_info.get('to_name', '')}
- 航班号: {flight_info['flight_no']}
- 航空公司: {flight_info.get('airline', '')}
- 出发日期: {flight_info['flight_date']}
- 起飞/落地: {flight_info.get('departure_time', '')} - {flight_info.get('arrival_time', '')}
- 当前价格: ¥{current_price}
- 价格变动: ¥{flight_info.get('old_price', 0)} → ¥{flight_info.get('new_price', current_price)} ({flight_info.get('change_amount', 0):+d}元)

## 历史价格统计
- 历史最低: ¥{min_price}
- 历史最高: ¥{max_price}
- 历史均价: ¥{avg_price:.0f}
- 当前价距离历史最低: +{pct_from_min:.0f}%
- 近 7 天趋势: {recent_trend}
- 距起飞还有: {days_until} 天

## 历史价格时间线
{timeline}

请用 2-3 句话分析（80 字以内）：
1. 当前价格处于什么水平（偏高 / 偏低 / 正常）
2. 未来可能的走势
3. 给出明确的购买建议（建议入手 / 再等等 / 尽快购买）

直接给出分析，不要用 markdown，不要序号。"""


def _call_deepseek(prompt):
    """调用 DeepSeek API"""
    resp = requests.post(
        "https://api.deepseek.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.DEEPSEEK_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": "你是机票价格分析专家。给出简洁实用、有人情味的购买建议。用中文，80 字以内。",
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 200,
            "temperature": 0.7,
        },
        timeout=30,
    )

    if resp.status_code != 200:
        raise Exception(f"API 返回 {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    content = data['choices'][0]['message']['content'].strip()
    print(f"   🤖 DeepSeek 分析完成 ({len(content)}字)")
    return f"🤖 {content}"


def _fallback_tip(flight_info, history, days_until):
    """API 失败时的兜底分析（纯统计学）"""
    prices = [h['price'] for h in history]
    if len(prices) < 3:
        return None

    min_p = min(prices)
    max_p = max(prices)
    avg_p = sum(prices) / len(prices)
    current = flight_info['price']
    day_info = f"距起飞 {days_until} 天" if days_until is not None else ""

    if current <= min_p:
        return f"🤖 📊 当前价格 ¥{current} 处于历史最低位，性价比很不错。{day_info}，建议关注入手时机。"
    elif current >= max_p:
        return f"🤖 📊 当前价格 ¥{current} 处于历史最高位，不着急的话可以等等看是否会回落。{day_info}"
    elif current < avg_p:
        return f"🤖 📊 当前价格 ¥{current} 低于历史均价 ¥{avg_p:.0f}，价格合适。{day_info}，建议持续关注。"
    else:
        return f"🤖 📊 当前价格 ¥{current} 略高于历史均价 ¥{avg_p:.0f}。{day_info}，可以观望一下再决定。"
