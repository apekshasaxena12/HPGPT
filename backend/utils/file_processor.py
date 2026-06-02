import PyPDF2
from PIL import Image

class FileProcessor:
    async def process_file(self, file_path: str, content_type: str):
        if content_type == "application/pdf":
            return self.extract_pdf_text(file_path)
        elif content_type.startswith("image/"):
            return self.process_image(file_path)
        else:
            return "File uploaded successfully"
    
    def extract_pdf_text(self, file_path: str):
        try:
            with open(file_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                text = ""
                for page in pdf_reader.pages:
                    text += page.extract_text()
                return text
        except Exception as e:
            return f"Error processing PDF: {str(e)}"
    
    def process_image(self, file_path: str):
        try:
            with Image.open(file_path) as img:
                return f"Image processed: {img.size[0]}x{img.size[1]} pixels, {img.format} format"
        except Exception as e:
            return f"Error processing image: {str(e)}"
