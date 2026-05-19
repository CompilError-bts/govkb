SITE_ANALYSIS_PROMPT = """
你是一个政府网站结构分析专家。请根据目标政府门户首页或栏目页HTML，识别最适合抓取“近3个月政府新闻/要闻”的新闻栏目。

分析优先级：
1. 优先选择本级政府综合新闻栏目，例如“咸阳新闻”“政务要闻”“本地要闻”“工作动态”。
2. 不要选择公告公示、政策文件、专题专栏、互动交流、部门入口、外链媒体栏目，除非页面没有综合新闻栏目。
3. 如果HTML中出现多个候选栏目，请选择最像持续发布本地政务新闻的列表页。
4. 链接可以是完整URL或相对URL，但必须来自HTML中真实存在的导航或列表链接。

只返回一个JSON对象，不要Markdown，不要解释。JSON结构如下：
{
  "portal_name": "网站名称",
  "news_section": {
    "name": "新闻栏目名称",
    "list_url": "新闻列表页URL",
    "article_link_pattern": "用于匹配文章链接的Python正则表达式",
    "date_location": "日期在HTML中的位置描述",
    "pagination": {
      "pattern": "分页URL模式，用{n}代替页码；无法判断则为空字符串",
      "total_pages_estimate": 0
    },
    "articles_per_page": 0
  },
  "article_page": {
    "title_selector": "标题CSS选择器、XPath或特征描述",
    "content_location": "正文CSS选择器、XPath或特征描述",
    "date_pattern": "用于匹配发布日期YYYY-MM-DD的Python正则表达式",
    "encoding": "页面编码，如utf-8、gb18030"
  },
  "confidence": 0.0,
  "reason": "一句话说明选择该栏目的依据"
}

HTML内容：
{html_content}
""".strip()


SITE_ANALYSIS_REPAIR_PROMPT = """
你上一次返回的网站结构无法通过抓取验证。请根据失败原因和HTML重新修正，只返回JSON对象。

失败原因：
{error}

原始JSON：
{previous_json}

HTML内容：
{html_content}
""".strip()


EXTRACT_REGEX_PROMPT = """
根据以下新闻列表HTML片段，写一个Python正则表达式来提取文章标题、链接和日期。

要求：
1. 正则必须包含3个命名分组：(?P<url>...)、(?P<title>...)、(?P<date>...)。
2. 日期格式为YYYY-MM-DD。
3. 链接格式大致为：{link_hint}
4. 只返回正则表达式，不要Markdown，不要解释。

HTML片段：
{html_snippet}
""".strip()


def build_site_analysis_prompt(html_content: str) -> str:
    return SITE_ANALYSIS_PROMPT.replace("{html_content}", html_content)


def build_site_analysis_repair_prompt(error: str, previous_json: str, html_content: str) -> str:
    return (
        SITE_ANALYSIS_REPAIR_PROMPT.replace("{error}", error)
        .replace("{previous_json}", previous_json)
        .replace("{html_content}", html_content)
    )


def build_extract_regex_prompt(html_snippet: str, link_hint: str = "") -> str:
    return EXTRACT_REGEX_PROMPT.replace("{html_snippet}", html_snippet).replace("{link_hint}", link_hint)
