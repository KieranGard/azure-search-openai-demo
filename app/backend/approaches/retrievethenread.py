from typing import Any, AsyncGenerator, Optional, Union

import openai
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import QueryType

from approaches.approach import Approach
from core.messagebuilder import MessageBuilder
from text import nonewlines


class RetrieveThenReadApproach(Approach):
    """
    Simple retrieve-then-read implementation, using the Cognitive Search and OpenAI APIs directly. It first retrieves
    top documents from search, then constructs a prompt with them, and then uses OpenAI to generate an completion
    (answer) with that prompt.
    """

    system_chat_template = (
        "You are an AI Human Resource Assistant and an expert in the supplied documents. "
        + "Your task is to help the Ipswich City Council employees gain insights from the documents and answer Human Resource questions. "
        + "Use 'you' to refer to the individual asking the questions even if they ask with 'I'. "
        + "Answer the following question using only the data provided in the supplied documents. "
        + "Provide clear and structured answers based on the context provided. Think step by step about information in retrieved documents to answer user queries. "
        + "Return any tables and relevant content as html. "
        + "When relevant, use bullet points and lists to structure your answer. "
        + "Each source has a name followed by colon and the actual information, always include the source name for each fact you use in the response. "
        + "If you cannot answer using the sources below, say you don't know. Use below example to answer"
    )

    # shots/sample conversation
    question = """
'Are uniforms provided to Council employees?'

Sources: 
Corporate Uniform Administrative Directive.pdf: The Council is committed to ensuring employees who are required to wear the compulsory corporate uniform are provided with comfortable and professional uniforms. Employees who are not required to wear the compulsory corporate uniform are provided with a variety of corporate clothing options that are comfortable, of good quality and able to be subsidised. Uniforms must comply with health and safety guidelines as relevant to the work being performed.
"""
    answer = "Yes, uniforms are provided to Council employees. The Corporate Uniform Administrative Directive states that Ipswich City Council aims to provide employees with comfortable and professional uniforms that project a professional image and comply with health and safety guidelines. The directive also states that employees who are required to wear the compulsory corporate uniform will be provided with comfortable and professional uniforms.[Corporate Uniform Administrative Directive.pdf]"

    def __init__(
        self,
        search_client: SearchClient,
        openai_host: str,
        chatgpt_deployment: Optional[str],  # Not needed for non-Azure OpenAI
        chatgpt_model: str,
        embedding_deployment: Optional[str],  # Not needed for non-Azure OpenAI or for retrieval_mode="text"
        embedding_model: str,
        sourcepage_field: str,
        content_field: str,
        query_language: str,
        query_speller: str,
    ):
        self.search_client = search_client
        self.openai_host = openai_host
        self.chatgpt_deployment = chatgpt_deployment
        self.chatgpt_model = chatgpt_model
        self.embedding_model = embedding_model
        self.embedding_deployment = embedding_deployment
        self.sourcepage_field = sourcepage_field
        self.content_field = content_field
        self.query_language = query_language
        self.query_speller = query_speller

    async def run(
        self,
        messages: list[dict],
        stream: bool = False,  # Stream is not used in this approach
        session_state: Any = None,
        context: dict[str, Any] = {},
    ) -> Union[dict[str, Any], AsyncGenerator[dict[str, Any], None]]:
        q = messages[-1]["content"]
        overrides = context.get("overrides", {})
        auth_claims = context.get("auth_claims", {})
        has_text = overrides.get("retrieval_mode") in ["text", "hybrid", None]
        has_vector = overrides.get("retrieval_mode") in ["vectors", "hybrid", None]
        use_semantic_captions = True if overrides.get("semantic_captions") and has_text else False
        top = overrides.get("top", 3)
        filter = self.build_filter(overrides, auth_claims)

        # If retrieval mode includes vectors, compute an embedding for the query
        if has_vector:
            embedding_args = {"deployment_id": self.embedding_deployment} if self.openai_host == "azure" else {}
            embedding = await openai.Embedding.acreate(**embedding_args, model=self.embedding_model, input=q)
            query_vector = embedding["data"][0]["embedding"]
        else:
            query_vector = None

        # Only keep the text query if the retrieval mode uses text, otherwise drop it
        query_text = q if has_text else ""

        # Use semantic ranker if requested and if retrieval mode is text or hybrid (vectors + text)
        if overrides.get("semantic_ranker") and has_text:
            r = await self.search_client.search(
                query_text,
                filter=filter,
                query_type=QueryType.SEMANTIC,
                query_language=self.query_language,
                query_speller=self.query_speller,
                semantic_configuration_name="default",
                top=top,
                query_caption="extractive|highlight-false" if use_semantic_captions else None,
                vector=query_vector,
                top_k=100 if query_vector else None,
                vector_fields="embedding" if query_vector else None,
            )
        else:
            r = await self.search_client.search(
                query_text,
                filter=filter,
                top=top,
                vector=query_vector,
                top_k=100 if query_vector else None,
                vector_fields="embedding" if query_vector else None,
            )
        if use_semantic_captions:
            results = [
                doc[self.sourcepage_field] + ": " + nonewlines(" . ".join([c.text for c in doc["@search.captions"]]))
                async for doc in r
            ]
        else:
            results = [doc[self.sourcepage_field] + ": " + nonewlines(doc[self.content_field]) async for doc in r]
        content = "\n".join(results)

        message_builder = MessageBuilder(
            overrides.get("prompt_template") or self.system_chat_template, self.chatgpt_model
        )

        # add user question
        user_content = q + "\n" + f"Sources:\n {content}"
        message_builder.insert_message("user", user_content)

        # Add shots/samples. This helps model to mimic response and make sure they match rules laid out in system message.
        message_builder.insert_message("assistant", self.answer)
        message_builder.insert_message("user", self.question)

        messages = message_builder.messages
        chatgpt_args = {"deployment_id": self.chatgpt_deployment} if self.openai_host == "azure" else {}
        chat_completion = await openai.ChatCompletion.acreate(
            **chatgpt_args,
            model=self.chatgpt_model,
            messages=messages,
            temperature=overrides.get("temperature") or 0.3,
            max_tokens=1024,
            n=1,
        )

        extra_info = {
            "data_points": results,
            "thoughts": f"Question:<br>{query_text}<br><br>Prompt:<br>"
            + "\n\n".join([str(message) for message in messages]),
        }
        chat_completion.choices[0]["context"] = extra_info
        chat_completion.choices[0]["session_state"] = session_state
        return chat_completion
