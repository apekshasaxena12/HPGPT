import os
import re
import io
import base64
import black
import pandas as pd
import plotly.express as px
import plotly.io as pio
import plotly.graph_objects as go
import google.generativeai as genai
from contextlib import redirect_stdout
from dotenv import load_dotenv
from langchain_community.document_loaders import PyPDFLoader
from autogen import AssistantAgent
import logging
import ast
import glob

logger = logging.getLogger(__name__)

load_dotenv()
api_key=os.getenv("GOOGLE_API_KEY")

class AnalyticsAgent(AssistantAgent):
    def __init__(self):
        super().__init__(
            name="AnalyticsAgent",
            system_message="You are a data analytics expert who writes Python code using pandas and Plotly."
        )
        genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
        self.model = genai.GenerativeModel("gemini-2.5-flash-lite") 

    def extract_code(self, text: str) -> str:
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:python)?", "", cleaned, flags=re.IGNORECASE | re.MULTILINE)
        cleaned = re.sub(r"```$", "", cleaned, flags=re.MULTILINE)
        cleaned = cleaned.lstrip("`").lstrip().replace("python", "", 1).lstrip()
        raw_code = cleaned.strip()

        raw_code = re.sub(
        r'(data\s*=\s*""".*?""")\)+',  # Match any closing parenthesis after multiline string
        r'\1',                         # Keep only the multiline assignment
        raw_code,
        flags=re.DOTALL | re.MULTILINE)
        raw_code = re.sub(r'^\s*\)\s*$', '', raw_code, flags=re.MULTILINE)
        return raw_code

    def generate_code_and_summary(self, df_sample: pd.DataFrame, sample_csv: str, columns: list, stats: str, user_prompt: str):

        prompt = f"""
You are a Python data analyst using pandas and Plotly.

You are given a DataFrame called `df`. Below is a **sample** of it and basic statistics for reference, but you must use `df` in your code — not just the sample.

Sample rows (CSV):
{sample_csv}

DataFrame statistics:
{stats}

User Query: {user_prompt}

INSTRUCTION:

1. Generate valid Python code using `df` to visualize the data using Plotly.
2. Give a brief textual summary of what the graph represents.
Strict rules:
- Do NOT randomly sample rows unless explicitly asked.
- Do not use sample().sort_values(...).
- Use correct numeric sorting (ascending or descending as per the request).
- Don't use df = pd.read_csv(...).
- Avoid fig.show().
- Do not include extra closing ')' after multiline string blocks like data = """""".


```python
# Code:
<code here>
```

Summary:
<one paragraph here>

Ensure the Python code is syntactically correct and executable without unmatched brackets or indentation errors.

"""
        response = self.model.generate_content(prompt)
        text = response.text

        code_match = re.search(r"```python\s*(.*?)```", text, re.DOTALL)
        summary_match = re.search(r"Summary:\s*(.*)", text, re.DOTALL)

        raw_code = self.extract_code(code_match.group(1)) if code_match else ""
        summary = summary_match.group(1).strip() if summary_match else "No summary provided."

        raw_code = re.sub(r"df\s*=\s*pd\.read_csv\(.*?\)", "", raw_code)
        raw_code = re.sub(r"\bfig\.show\(\)", "", raw_code)

        try:
            return black.format_str(raw_code, mode=black.Mode()), summary
        except Exception:
            return raw_code, summary

    def load_file(self, file: dict) -> pd.DataFrame:
        filename = file["name"]
        path = file.get("path")
        content = file.get("content")

        # ✅ Prefer reading from disk if path is provided
        if path and os.path.exists(path):
            logger.info(f"📂 Loading file from disk: {path}")
            if filename.endswith(".csv"):
                return pd.read_csv(path)
            elif filename.endswith(".xlsx"):
                return pd.read_excel(path, engine="openpyxl")

        # ✅ Fallback to in-memory content
        if filename.endswith(".csv") and content:
            return pd.read_csv(io.StringIO(content))

        elif filename.endswith(".xlsx") and content:
            if isinstance(content, bytes):
                content_bytes = content
            elif isinstance(content, str):
                content_bytes = base64.b64decode(content)
            else:
                raise ValueError("Unsupported content type for Excel file.")
            return pd.read_excel(io.BytesIO(content_bytes), engine="openpyxl")

        elif filename.endswith(".pdf") and content:
            os.makedirs("temp", exist_ok=True)
            temp_path = os.path.join("temp", filename)
            with open(temp_path, "wb") as f:
                f.write(content.encode())
            loader = PyPDFLoader(temp_path)
            pages = loader.load()
            combined_text = "\n".join([p.page_content for p in pages])
            raise ValueError("PDF content is not yet supported for graph generation:\n\n" + combined_text[:3000])

        else:
            raise ValueError("Unsupported file format. Use .csv, .xlsx, or .pdf.")
        
    def get_latest_uploaded_file(self) -> dict:
        files = glob.glob("uploads/*")
        if not files:
            raise FileNotFoundError("No uploaded files found in 'uploads/' directory.")
        
        latest_file = max(files, key=os.path.getmtime)
        with open(latest_file, "rb") as f:
            content = f.read()

        filename = os.path.basename(latest_file)
        return {
            "name": filename,
            "path": latest_file,
            "content": content
        }
    def is_graph_required(self, user_prompt: str) -> bool:
        reasoning_prompt = f"""
                You're a data analyst. The user asks: "{user_prompt}"
                Do you need to generate a plotly graph to answer this, or is a plain data analysis enough?

                Answer only: "yes" or "no"
                """
        resp = self.model.generate_content(reasoning_prompt).text.lower()
        return "yes" in resp
    
    def generate_analysis_code(self, df: pd.DataFrame, sample_csv: str, stats: str, user_prompt: str) -> str:
        prompt = f"""
        You are a Python data analyst using pandas.

        You are given a sample of a DataFrame called `df_sample`, derived from a full DataFrame `df`.
        Do not use `df_sample` in your code — always write logic assuming the full DataFrame is named `df`.

        Sample rows (CSV):
        {sample_csv}

        DataFrame statistics:
        {stats}

        User Query: {user_prompt}

        INSTRUCTIONS:

        1. Write Python code to answer the query using the full DataFrame `df`.
        2. Do NOT use `df.read_csv`, `df.sample()`, or hardcoded values unless explicitly instructed.
        3. You MUST use `print(...)` to display the result. Do not return or evaluate expressions silently.
        4. Avoid assumptions about column names — rely only on the provided sample and stats.
        5. If comparing string values (e.g., Brand == "Maruti"), always use:
        `df["Brand"].str.strip().str.lower() == "maruti"` to ensure consistent matching.
        6. Do NOT use `pandas.compat.StringIO` — it is deprecated and will cause an error. Use `io.StringIO` if needed.
        7. Wrap only the code inside triple backticks like this:

        ```python
        <your code>
        Below the code block, write a brief summary sentence of the result as plain text (no backticks, no markdown).

        Example:

        Count rows for Brand == 'Maruti'
        print(df[df["Brand"].str.strip().str.lower() == "maruti"].shape[0]) is the count of rows for Brand 'Maruti'.
        """
        
        response = self.model.generate_content(prompt).text

        code_match = re.search(r"```python\s*(.*?)```", response, re.DOTALL)
        summary_match = re.search(r"```(?:python)?\s*.*?```\s*(.+)", response, re.DOTALL)

        code = code_match.group(1).strip() if code_match else ""
        summary = summary_match.group(1).strip() if summary_match else "No summary provided."

        return self.extract_code(code), summary

    def execute_and_rephrase_code(
        self,
        df: pd.DataFrame,
        code: str,
        user_prompt: str
    ) -> dict:
        """
        Executes provided code on the DataFrame `df`, captures output,
        and rephrases it using the LLM to produce a natural-language summary.
        """
        local_vars = {"pd": pd, "df": df}
        buffer = io.StringIO()

        with redirect_stdout(buffer):
            try:
                ast.parse(code)  # Check syntax first
                exec(code, local_vars)
            except Exception as e:
                return {
                    "error": f"❌ Error during code execution:\n\n{e}\n\nCode:\n{code}",
                    "code": code,
                    "agent_type": "analytics"
                }

        output = buffer.getvalue().strip()

        if not output:
            return {
                "error": "❌ No output captured. Ensure the code uses print(...) to show results.",
                "code": code,
                "agent_type": "analytics"
            }

        rephrase_prompt = f"""
    You are a helpful assistant. The user asked:
    "{user_prompt}"

    The Python code produced this output:
    {output}

    Rephrase this as a concise, friendly sentence that directly answers the user's query.
    Final answer:
    """
        try:
            rephrased = self.model.generate_content(rephrase_prompt).text.strip()
        except Exception as e:
            rephrased = f"(Could not rephrase due to LLM error: {e})"

        return {
            "response": "",
            "summary": rephrased,
            "code": code,
            "agent_type": "analytics"
        }



    async def run(self, file: dict = None, user_prompt: str = "") -> dict:

        code = ""
        try:
            if file is None:
                file = self.get_latest_uploaded_file()

            df = self.load_file(file)
            logger.info(f"📥 Loaded DataFrame shape: {df.shape}")
            logger.info(f"📄 Columns: {df.columns.tolist()}")
            logger.info(f"🔍 First few rows:\n{df.head().to_string()}")

            if df is None or df.empty:
                return {"error": "❌ No valid data found in the uploaded file."}

            df_sample = df.sample(min(len(df), 10), random_state=42) # more diverse than head()
            sample_csv = df_sample.to_csv(index=False)
            columns = df.columns.tolist()
            stats = df.describe(include='all').to_string()

            # Optional: include additional stats like unique counts, nulls, dtypes
            extra_info = pd.DataFrame({
                "dtype": df.dtypes,
                "nulls": df.isnull().sum(),
                "unique": df.nunique()
            }).to_string()

            if self.is_graph_required(user_prompt):
                code, summary = self.generate_code_and_summary(df_sample, sample_csv, columns, stats + "\n\n" + extra_info, user_prompt)
                is_plot = True
            else:
                code, summary = self.generate_analysis_code(df_sample, sample_csv, stats + "\n\n" + extra_info, user_prompt)
                is_plot = False

            
            local_vars = {"pd": pd, "px": px, "go": go, "df": df}
            buffer = io.StringIO()

            with redirect_stdout(buffer):
                                try:
                                    try:
                                        ast.parse(code)
                                    except SyntaxError as e:
                                        return {
                                            "error": f"❌ Pre-execution syntax error:\n\n{e}\n\npython\n{code}\n"
                                        }

                                    exec(code, local_vars)
                                    if not is_plot:
                                        return self.execute_and_rephrase_code(df=df, code=code, user_prompt=user_prompt)



                                    
                                except SyntaxError as e:
                                    return {
                                        "error": f"❌ Syntax error in generated code:\n\n{e}\n\npython\n{code}\n"
                                        }

            figs = [v for v in local_vars.values() if isinstance(v, go.Figure)]
            if not figs:
                return {
                    "response": "Code executed but no graph was returned.",
                    "code": code,
                    "summary": summary,
                    "agent_type": "analytics"
                }

            html_parts = []
            for i, fig in enumerate(figs):
                fig.update_layout(
                    autosize=True,
                    width=None,
                    height=None,
                    margin=dict(l=10, r=10, t=40, b=20),
                )
                html = pio.to_html(
                    fig,
                    full_html=False,
                    include_plotlyjs="cdn" if i == 0 else False,  # type: ignore[arg-type]
                    config={"responsive": True}
                )
                html_parts.append(html)

            return {
                "response": "\n".join(html_parts),
                "summary": summary,
                "plot_graph": "\n".join(html_parts),
                "code": code,
                "agent_type": "analytics"
            }


        except Exception as e:
            return {
                "response": f"❌ Error:\n\n{str(e)}",
                "code": code,
                "agent_type": "analytics"
            }
