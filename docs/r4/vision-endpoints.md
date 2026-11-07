# Vision Endpoints With BentoML

R4 vision endpoints use the same UMRS-owned endpoint lifecycle as LLM endpoints, but the runtime service is BentoML instead of LiteLLM/vLLM.

## Register Or Stage A Vision Model

A vision model entry should use `model_type=vision` and a BentoML serving profile. For existing weights on shared storage:

```bash
r4 model register sam3 \
  --type vision \
  --path /shared/models/sam3 \
  --backend bentoml \
  --service-kind bentoml_vision \
  --launch-profile harness \
  --launch-profile vision-bentoml \
  --vision-adapter sam3
```

The default vision launch is a separate BentoML image/profile so startup avoids the vLLM path unless it is explicitly requested. To co-locate vLLM for a combined endpoint, pass `--co-locate-vllm` when provisioning.

## Provision The Endpoint

```bash
r4 endpoint up \
  --model sam3 \
  --backend bentoml \
  --service-kind bentoml_vision \
  --wait
```

UMRS resolves vision endpoints to:

- serving backend: `bentoml`
- service kind: `bentoml_vision`
- launch profiles: `harness vision-bentoml`
- readiness probe: HTTP `GET /readyz`
- runtime env: `VISION_REQUIRE_PRESIGNED_URLS=true`, `TRANSFORMERS_OFFLINE=1`, `HF_HUB_OFFLINE=1`

Container image overrides can be set on the model profile or at launch:

```bash
r4 endpoint up --model sam3 --backend bentoml --container-image <your-registry>/umrs-vision:<tag> --wait
```

The image is not published to a public registry: build it from
`umrs/src/umrs/vision_service/Dockerfile` (build context `umrs/`) and push it to a registry your
cluster can pull from.

## Submit Inference

```bash
r4 vision submit ./cell.png \
  --model sam3 \
  --adapter sam3 \
  --config prompt=cell
```

For local files, `r4` uploads through `POST /gw/files/upload`; UMRS stores the file in project-scoped S3 and returns an object URI. The client never receives presigned URLs. `POST /gw/vision/infer/submit` asks UMRS to discover the ready BentoML endpoint, generate internal presigned input/output URLs, and forward the task to BentoML.

You can also submit an existing project-scoped object:

```bash
r4 vision submit s3://umrs-files/<project-id>/images/cell.png --model sam3 --adapter sam3
```

Then inspect or fetch the task:

```bash
r4 vision status <task-id> --endpoint-id <endpoint-job-id>
r4 vision get <task-id> --endpoint-id <endpoint-job-id> --output result.pkl
r4 vision cancel <task-id> --endpoint-id <endpoint-job-id>
```

## Boundary Notes

- Gateway is still the only external HTTP surface: `r4 -> Gateway -> UMRS -> BentoML`.
- UMRS owns endpoint discovery, readiness, presigned URL creation, and task forwarding.
- Presigned URLs are only generated inside UMRS for the compute-side BentoML service.
- Inference containers stay offline; model files must already be staged or mounted.
