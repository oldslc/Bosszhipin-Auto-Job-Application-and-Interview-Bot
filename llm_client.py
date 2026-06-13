"""
LLM API 客户端
封装 OpenAI 兼容的 Chat Completions 接口调用
支持流式/非流式响应、错误重试
"""

import json
import logging
import time
from typing import List, Dict, Optional, Generator

import requests

import config

logger = logging.getLogger(__name__)


class LLMClient:
    """
    LLM API 客户端
    兼容 OpenAI Chat Completions 格式的 API（如 WebAI2API、OpenAI、vLLM 等）
    """

    def __init__(self,
                 api_url: str = None,
                 model: str = None,
                 api_key: str = None,
                 timeout: int = None,
                 max_retries: int = None):
        self.api_url = api_url or config.LLM_API_URL
        self.model = model or config.LLM_MODEL
        self.api_key = api_key or getattr(config, "LLM_API_KEY", "")
        self.timeout = timeout or config.LLM_TIMEOUT
        self.max_retries = max_retries or config.LLM_MAX_RETRIES

    def _build_headers(self) -> dict:
        """构建请求头"""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _build_payload(self, messages: List[Dict[str, str]],
                       temperature: float = 0.7,
                       max_tokens: int = 500,
                       stream: bool = False) -> dict:
        """构建请求体"""
        return {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream
        }

    # ----------------------------------------------------------
    # 非流式调用
    # ----------------------------------------------------------
    def chat(self, messages: List[Dict[str, str]],
             temperature: float = 0.7,
             max_tokens: int = 500) -> Optional[str]:
        """
        发送聊天请求（非流式）
        返回 LLM 生成的文本，失败返回 None
        """
        payload = self._build_payload(messages, temperature, max_tokens, stream=False)
        headers = self._build_headers()

        for attempt in range(1, self.max_retries + 1):
            try:
                logger.debug(f"LLM 请求 (尝试 {attempt}/{self.max_retries})")
                resp = requests.post(
                    self.api_url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout
                )
                resp.raise_for_status()
                data = resp.json()

                # 提取回复内容
                content = data["choices"][0]["message"]["content"].strip()
                logger.debug(f"LLM 回复: {content[:100]}...")
                return content

            except requests.exceptions.Timeout:
                logger.warning(f"LLM 请求超时 (尝试 {attempt}/{self.max_retries})")
            except requests.exceptions.ConnectionError:
                logger.error(f"无法连接到 LLM API: {self.api_url}")
                if attempt == self.max_retries:
                    return None
            except requests.exceptions.HTTPError as e:
                logger.error(f"LLM API 返回错误: {e}")
                if attempt == self.max_retries:
                    return None
            except (KeyError, IndexError, json.JSONDecodeError) as e:
                logger.error(f"LLM 响应解析失败: {e}")
                if attempt == self.max_retries:
                    return None

            # 指数退避
            if attempt < self.max_retries:
                wait = 2 ** attempt
                logger.info(f"等待 {wait} 秒后重试...")
                time.sleep(wait)

        return None

    # ----------------------------------------------------------
    # 流式调用
    # ----------------------------------------------------------
    def chat_stream(self, messages: List[Dict[str, str]],
                    temperature: float = 0.7,
                    max_tokens: int = 500) -> Generator[str, None, None]:
        """
        发送聊天请求（流式）
        逐块 yield 文本片段
        """
        payload = self._build_payload(messages, temperature, max_tokens, stream=True)
        headers = self._build_headers()

        try:
            resp = requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
                stream=True
            )
            resp.raise_for_status()

            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                # SSE 格式: data: {...}
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk["choices"][0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

        except Exception as e:
            logger.error(f"流式 LLM 请求失败: {e}")
            yield ""

    # ----------------------------------------------------------
    # 便捷方法：直接传 prompt 文本
    # ----------------------------------------------------------
    def ask(self, system_prompt: str, user_prompt: str,
            temperature: float = 0.7, max_tokens: int = 500) -> Optional[str]:
        """
        简化调用：传入系统提示 + 用户提示，返回回复文本
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        return self.chat(messages, temperature, max_tokens)
