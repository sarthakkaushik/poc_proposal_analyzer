from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse
import tempfile
import os
from langchain_community.vectorstores import FAISS
from langchain_community.document_loaders import PyPDFLoader
from langchain_openai import OpenAIEmbeddings, AzureChatOpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate
from langchain_openai import AzureOpenAIEmbeddings
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.pydantic_v1 import BaseModel, Field
from typing import List
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# Azure OpenAI settings
AZURE_OPENAI_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_DEPLOYMENT = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME")


import tempfile

def load_document(file):
  with tempfile.SpooledTemporaryFile(mode='wb') as tmp_file:
      tmp_file.write(file.read())
      tmp_file.seek(0)
      loader = PyPDFLoader(tmp_file.name)
      documents = loader.load()
      text_splitter = RecursiveCharacterTextSplitter(chunk_size=1024, chunk_overlap=0)
      docs = text_splitter.split_documents(documents)
  return docs

def create_vector_db(docs, path):
  embedding_function = AzureOpenAIEmbeddings(
      openai_api_type="azure",
      openai_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
      azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
      deployment="text-embedding-ada-002",
      model="text-embedding-ada-002",
  )
  db = FAISS.from_documents(docs, embedding_function)
  db.save_local(path)
  return db


def load_vector_db(path):
  embedding_function = AzureOpenAIEmbeddings(
      openai_api_type="azure",
      openai_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
      azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
      deployment="text-embedding-ada-002",
      model="text-embedding-ada-002",
  )
  return FAISS.load_local(
      path, embedding_function, allow_dangerous_deserialization=True
  )


def genrating_eligbility(rfp_text):
  llm = AzureChatOpenAI(
      openai_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
      openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
      azure_deployment=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME"),
      azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
      temperature=0,
  )

  template = """
  You are an AI contract analyzer. Your task is to Find all the eligibility criteria listed in a Request for Proposal (RFP).

  RFP Content:
  {rfp_text}

  """

  prompt = PromptTemplate(input_variables=["rfp_text"], template=template)
  chain = LLMChain(llm=llm, prompt=prompt)

  return chain.run(rfp_text=rfp_text)


def analyze_eligibility(rfp_content, proposal_content):

  class EligibilityCriterion(BaseModel):
      criterion: str = Field(
          ..., description="Description of the eligibility criterion."
      )
      eligibility_met: str = Field(
          ..., description="Whether the eligibility criterion is met (Yes/No)."
      )
      reason: str = Field(..., description="Reason for the eligibility status.")

  class EligibilityData(BaseModel):
      eligibility_criteria: List[EligibilityCriterion]

  parser = JsonOutputParser(pydantic_object=EligibilityData)

  llm = AzureChatOpenAI(
      openai_api_key=os.getenv("AZURE_OPENAI_API_KEY"),
      openai_api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
      azure_deployment=os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT_NAME"),
      azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
      temperature=0,
  )

  template = """
  You are an AI contract analyzer. Your task is to compare the eligibility criteria listed in a Request for Proposal (RFP) with the details provided in a proposal.

  RFP Content:
  {rfp_content}

  Proposal Content:
  {proposal_content}

  For each eligibility criterion in the RFP, provide the following:

  Eligibility Criterion: [Insert eligibility criterion from RFP]
  Eligibility Met (Yes/No): [Yes/No]
  Reason: [Provide a detailed explanation of how the eligibility criterion is met or not met based on the proposal]

  Please provide your analysis in a clear, structured format.
  \n{format_instructions}
  """

  prompt = PromptTemplate(
      template=template,
      input_variables=["rfp_content", "proposal_content"],
      partial_variables={"format_instructions": parser.get_format_instructions()},
  )

  json_chain = prompt | llm | parser

  result = json_chain.invoke(
      {"rfp_content": rfp_content, "proposal_content": proposal_content}
  )

  return result



@app.post("/analyze/")
async def analyze(rfp_file: UploadFile = File(...), proposal_file: UploadFile = File(...)):
  if not all([AZURE_OPENAI_KEY, AZURE_OPENAI_ENDPOINT, AZURE_DEPLOYMENT]):
      raise HTTPException(status_code=500, detail="Azure OpenAI settings are not properly configured")

  try:
      # Load and process RFP
      rfp_docs = load_document(rfp_file.file)
      rfp_db = create_vector_db(rfp_docs, f"vectorstore/RFP/{rfp_file.filename}")

      # Load and process Proposal
      proposal_docs = load_document(proposal_file.file)
      proposal_db = create_vector_db(proposal_docs, f"vectorstore/Proposals/{proposal_file.filename}")

      # Retrieve relevant content
      rfp_content = rfp_db.similarity_search("eligibility criteria", k=20)
      proposal_content = proposal_db.similarity_search("company background and qualifications", k=20)

      # Combine retrieved content
      rfp_text = " ".join([doc.page_content for doc in rfp_content])
      proposal_text = " ".join([doc.page_content for doc in proposal_content])

      # Generate RFP Eligibility
      ref_eligibility = genrating_eligbility(rfp_text)

      # Analyze eligibility
      analysis = analyze_eligibility(ref_eligibility, proposal_text)

      return JSONResponse(content=analysis.dict())
  except FileNotFoundError:
      raise HTTPException(status_code=404, detail="File not found")
  except ValueError as ve:
      raise HTTPException(status_code=400, detail=str(ve))
  except Exception as e:
      raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

if __name__ == "__main__":
  import uvicorn
  uvicorn.run(app, host="0.0.0.0", port=8000)