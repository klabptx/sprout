FROM public.ecr.aws/lambda/python:3.11

# Copy package metadata and source code.
COPY pyproject.toml ${LAMBDA_TASK_ROOT}/
COPY sprout/ ${LAMBDA_TASK_ROOT}/sprout/

# Install the package with the openai extra (needed for LLM synthesis).
RUN pip install --no-cache-dir "${LAMBDA_TASK_ROOT}[openai]"

# Copy the protobuf file used for event-code enrichment.
COPY SystemLog.proto ${LAMBDA_TASK_ROOT}/

CMD ["sprout.lambda_handler.handler"]
