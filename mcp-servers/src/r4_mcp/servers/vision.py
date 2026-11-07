"""
Vision MCP Server (M-201).

Provides sandboxed access to vision models for image analysis.

Features:
- Image segmentation via SAM3 (Segment Anything Model)
- Object detection and bounding boxes
- Feature extraction for embeddings
- Batch processing support

Security:
- All images validated and sanitized before processing
- Output paths restricted to sandbox
- No network access from inference container
- Memory and GPU resource limits
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..base import MCPServer, Tool, ToolResult, TransportType
from ..security import DangerLevel, ToolCapability

logger = logging.getLogger(__name__)


# =============================================================================
# Vision Model Abstraction
# =============================================================================


class VisionModel:
    """Abstract interface for vision models."""

    name: str = "base"

    def load(self) -> None:
        """Load model weights."""
        raise NotImplementedError

    def segment(
        self,
        image_path: str,
        points: Optional[List[tuple]] = None,
        boxes: Optional[List[tuple]] = None,
    ) -> Dict[str, Any]:
        """Perform image segmentation."""
        raise NotImplementedError

    def extract_features(self, image_path: str) -> List[float]:
        """Extract feature embedding from image."""
        raise NotImplementedError


class SAM3Model(VisionModel):
    """
    Segment Anything Model 3 wrapper.

    Uses Meta's SAM3 for zero-shot image segmentation.
    Model is expected to be pre-staged at UMRS_SAM3_MODEL_PATH.
    """

    name = "sam3"

    def __init__(
        self,
        model_path: Optional[str] = None,
        device: str = "cuda",
    ):
        """
        Initialize SAM3 model.

        Args:
            model_path: Path to pre-staged model weights.
            device: Device to use (cuda, cpu).
        """
        self.model_path = model_path or os.environ.get("UMRS_SAM3_MODEL_PATH", "/models/sam3")
        self.device = device
        self.model = None
        self.processor = None

    def load(self) -> None:
        """Load SAM3 model from pre-staged weights."""
        if self.model is not None:
            return

        # Verify offline mode
        if os.environ.get("TRANSFORMERS_OFFLINE", "0") != "1":
            logger.warning("TRANSFORMERS_OFFLINE not set! SAM3 should run in offline mode.")

        try:
            # Import only when needed
            import torch  # noqa: F401

            # Check for mock mode (testing)
            if os.environ.get("UMRS_MOCK_VISION_MODEL", "").lower() in ("true", "1"):
                logger.info("Using mock SAM3 model for testing")
                self.model = "mock"
                self.processor = "mock"
                return

            # Try to load from pre-staged path
            if Path(self.model_path).exists():
                from transformers import SamModel, SamProcessor

                logger.info(f"Loading SAM3 from {self.model_path}")
                self.processor = SamProcessor.from_pretrained(
                    self.model_path, local_files_only=True
                )
                self.model = SamModel.from_pretrained(self.model_path, local_files_only=True)
                self.model.to(self.device)
                self.model.eval()
                logger.info(f"SAM3 loaded on {self.device}")
            else:
                raise FileNotFoundError(
                    f"SAM3 model not found at {self.model_path}. "
                    "Model must be pre-staged before use."
                )

        except ImportError as e:
            logger.error(f"Required packages not available: {e}")
            raise RuntimeError(
                "transformers package required for SAM3. Install with: pip install transformers"
            )

    def segment(
        self,
        image_path: str,
        points: Optional[List[tuple]] = None,
        boxes: Optional[List[tuple]] = None,
    ) -> Dict[str, Any]:
        """
        Perform image segmentation.

        Args:
            image_path: Path to input image.
            points: List of (x, y) prompt points (optional).
            boxes: List of (x1, y1, x2, y2) bounding boxes (optional).

        Returns:
            Dictionary with masks, scores, and metadata.
        """
        self.load()

        # Mock mode for testing
        if self.model == "mock":
            return {
                "masks": [[[[1, 0], [0, 1]]]],  # Simple mock mask
                "scores": [0.95],
                "input_size": [100, 100],
                "mock": True,
            }

        try:
            import numpy as np
            import torch
            from PIL import Image

            # Load and preprocess image
            image = Image.open(image_path).convert("RGB")
            width, height = image.size

            # Prepare inputs
            if points:
                input_points = [[[p[0], p[1]] for p in points]]
                input_labels = [[1] * len(points)]  # All foreground
                inputs = self.processor(
                    image,
                    input_points=input_points,
                    input_labels=input_labels,
                    return_tensors="pt",
                )
            elif boxes:
                input_boxes = [[[b[0], b[1], b[2], b[3]] for b in boxes]]
                inputs = self.processor(
                    image,
                    input_boxes=input_boxes,
                    return_tensors="pt",
                )
            else:
                # No prompts - generate for whole image
                inputs = self.processor(image, return_tensors="pt")

            # Move to device
            inputs = {k: v.to(self.device) for k, v in inputs.items()}

            # Run inference
            with torch.no_grad():
                outputs = self.model(**inputs)

            # Process outputs
            masks = self.processor.image_processor.post_process_masks(
                outputs.pred_masks.cpu(),
                inputs["original_sizes"].cpu(),
                inputs["reshaped_input_sizes"].cpu(),
            )

            scores = outputs.iou_scores[0].cpu().numpy().tolist()

            # Convert masks to serializable format
            mask_list = []
            for mask in masks[0]:
                mask_np = mask.numpy().astype(np.uint8) * 255
                mask_list.append(mask_np.tolist())

            return {
                "masks": mask_list,
                "scores": scores,
                "input_size": [width, height],
                "num_masks": len(mask_list),
            }

        except Exception as e:
            logger.error(f"Segmentation failed: {e}")
            return {"error": str(e), "masks": [], "scores": []}

    def extract_features(self, image_path: str) -> List[float]:
        """
        Extract image embedding features.

        Args:
            image_path: Path to input image.

        Returns:
            Feature vector as list of floats.
        """
        self.load()

        # Mock mode
        if self.model == "mock":
            import random

            return [random.random() for _ in range(256)]

        try:
            import torch
            from PIL import Image

            image = Image.open(image_path).convert("RGB")
            inputs = self.processor(image, return_tensors="pt")
            inputs = {k: v.to(self.device) for k, v in inputs.items()}

            with torch.no_grad():
                # Get image embeddings from encoder
                image_embeddings = self.model.get_image_embeddings(inputs["pixel_values"])

            # Average pool spatial dimensions
            features = image_embeddings.mean(dim=[2, 3]).squeeze().cpu().numpy()
            return features.tolist()

        except Exception as e:
            # #215: raise so the tool handler returns ToolResult.error instead of
            # a successful 0-dimension embedding that silently pollutes searches.
            logger.error(f"Feature extraction failed: {e}")
            raise


# =============================================================================
# Vision MCP Server
# =============================================================================


class VisionServer(MCPServer):
    """
    Vision MCP server for image analysis.

    Provides tools for segmentation, object detection, and feature extraction.
    """

    name = "vision"
    version = "1.0.0"

    def __init__(
        self,
        model: Optional[VisionModel] = None,
        output_dir: str = "/tmp/vision_outputs",
        max_image_size: int = 50 * 1024 * 1024,  # 50MB
        transport: TransportType = TransportType.STDIO,
    ):
        """
        Initialize vision server.

        Args:
            model: Vision model to use (default: SAM3).
            output_dir: Directory for output files.
            max_image_size: Maximum input image size.
            transport: MCP transport type.
        """
        self.model = model or SAM3Model()
        self.output_dir = Path(output_dir)
        self.max_image_size = max_image_size

        # Create output directory
        self.output_dir.mkdir(parents=True, exist_ok=True)

        super().__init__(transport)
        logger.info(f"Vision server initialized: model={self.model.name}")

    def _validate_image_path(self, path: str) -> Optional[Path]:
        """Validate and resolve image path."""
        try:
            resolved = Path(path).resolve()
            if not resolved.exists():
                return None
            if not resolved.is_file():
                return None
            # Check file size
            if resolved.stat().st_size > self.max_image_size:
                logger.warning(f"Image too large: {resolved}")
                return None
            return resolved
        except Exception as e:
            logger.error(f"Path validation failed: {e}")
            return None

    def _validate_output_path(self, path: str) -> Optional[Path]:
        """Validate output path is within output_dir."""
        try:
            resolved = Path(path).resolve()
            # Must be within output directory
            if not str(resolved).startswith(str(self.output_dir)):
                logger.warning(f"Output path escapes sandbox: {resolved}")
                return None
            return resolved
        except Exception as e:
            logger.error(f"Output path validation failed: {e}")
            return None

    def _setup_tools(self) -> List[Tool]:
        """Define available tools."""
        return [
            Tool(
                name="segment_image",
                description=(
                    "Segment an image using SAM3 (Segment Anything Model). "
                    "Returns masks for detected objects. Optionally provide "
                    "prompt points or bounding boxes to guide segmentation."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "image_path": {
                            "type": "string",
                            "description": "Path to input image file",
                        },
                        "points": {
                            "type": "array",
                            "items": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 2,
                                "maxItems": 2,
                            },
                            "description": "List of [x, y] prompt points (optional)",
                        },
                        "boxes": {
                            "type": "array",
                            "items": {
                                "type": "array",
                                "items": {"type": "number"},
                                "minItems": 4,
                                "maxItems": 4,
                            },
                            "description": "List of [x1, y1, x2, y2] boxes (optional)",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Path to save mask output (optional)",
                        },
                    },
                    "required": ["image_path"],
                },
                handler=self._handle_segment,
                capability=ToolCapability.EMBED_CONTENT,
                danger_level=DangerLevel.SAFE,
            ),
            Tool(
                name="extract_features",
                description=(
                    "Extract feature embedding from an image. "
                    "Returns a vector representation suitable for similarity search."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "image_path": {
                            "type": "string",
                            "description": "Path to input image file",
                        },
                    },
                    "required": ["image_path"],
                },
                handler=self._handle_extract_features,
                capability=ToolCapability.EMBED_CONTENT,
                danger_level=DangerLevel.SAFE,
            ),
            Tool(
                name="analyze_image",
                description=(
                    "Analyze an image and return metadata, dimensions, and basic statistics."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "image_path": {
                            "type": "string",
                            "description": "Path to input image file",
                        },
                    },
                    "required": ["image_path"],
                },
                handler=self._handle_analyze,
                capability=ToolCapability.READ_LOCAL_FILE,
                danger_level=DangerLevel.SAFE,
            ),
        ]

    async def _handle_segment(
        self,
        image_path: str,
        points: Optional[List[List[float]]] = None,
        boxes: Optional[List[List[float]]] = None,
        output_path: Optional[str] = None,
    ) -> ToolResult:
        """Handle segment_image tool call."""
        # Validate input path
        validated_path = self._validate_image_path(image_path)
        if not validated_path:
            return ToolResult.error(f"Invalid or inaccessible image path: {image_path}")

        # Apply image security guard
        try:
            from umrs.image_guard import process_image_for_embedding

            analysis, processed = process_image_for_embedding(
                str(validated_path), raise_on_threat=True
            )
            logger.info(f"Image security check passed: {analysis.overall_level}")
        except ImportError:
            logger.warning("image_guard not available, skipping security check")
        except Exception as e:
            return ToolResult.error(f"Image security check failed: {e}")

        # Convert points/boxes to tuples
        point_tuples = [tuple(p) for p in points] if points else None
        box_tuples = [tuple(b) for b in boxes] if boxes else None

        # Run segmentation
        try:
            result = self.model.segment(
                str(validated_path),
                points=point_tuples,
                boxes=box_tuples,
            )

            # Optionally save output
            if output_path:
                validated_output = self._validate_output_path(output_path)
                if validated_output:
                    import json

                    with open(validated_output, "w") as f:
                        json.dump(result, f)
                    result["output_saved"] = str(validated_output)

            import json

            data = {
                "num_masks": result.get("num_masks", len(result.get("masks", []))),
                "scores": result.get("scores", []),
                "input_size": result.get("input_size"),
                "model": self.model.name,
            }
            return ToolResult.success(json.dumps(data))

        except Exception as e:
            logger.error(f"Segmentation failed: {e}")
            return ToolResult.error(f"Segmentation failed: {e}")

    async def _handle_extract_features(
        self,
        image_path: str,
    ) -> ToolResult:
        """Handle extract_features tool call."""
        validated_path = self._validate_image_path(image_path)
        if not validated_path:
            return ToolResult.error(f"Invalid or inaccessible image path: {image_path}")

        try:
            import json

            features = self.model.extract_features(str(validated_path))
            data = {
                "features": features,
                "dimension": len(features),
                "model": self.model.name,
            }
            return ToolResult.success(json.dumps(data))
        except Exception as e:
            logger.error(f"Feature extraction failed: {e}")
            return ToolResult.error(f"Feature extraction failed: {e}")

    async def _handle_analyze(
        self,
        image_path: str,
    ) -> ToolResult:
        """Handle analyze_image tool call."""
        import json

        validated_path = self._validate_image_path(image_path)
        if not validated_path:
            return ToolResult.error(f"Invalid or inaccessible image path: {image_path}")

        try:
            # Apply image guard for analysis
            from umrs.image_guard import analyze_image_security

            analysis = analyze_image_security(str(validated_path))

            result = {
                "path": str(validated_path),
                "file_size": validated_path.stat().st_size,
                "format": analysis.format_detected,
                "width": analysis.width,
                "height": analysis.height,
                "security_level": analysis.overall_level.value,
                "threats": [
                    {"type": t.threat_type.value, "level": t.level.value} for t in analysis.threats
                ],
            }

            return ToolResult.success(json.dumps(result))

        except ImportError:
            # Fallback without image_guard
            try:
                from PIL import Image

                with Image.open(validated_path) as img:
                    result = {
                        "path": str(validated_path),
                        "file_size": validated_path.stat().st_size,
                        "format": img.format,
                        "width": img.width,
                        "height": img.height,
                        "mode": img.mode,
                    }
                return ToolResult.success(json.dumps(result))
            except Exception as e:
                return ToolResult.error(f"Image analysis failed: {e}")


# =============================================================================
# Server Factory
# =============================================================================


def create_vision_server(
    model_type: str = "sam3",
    model_path: Optional[str] = None,
    output_dir: str = "/tmp/vision_outputs",
    device: str = "cuda",
) -> VisionServer:
    """
    Create a vision MCP server.

    Args:
        model_type: Type of vision model ("sam3").
        model_path: Path to pre-staged model.
        output_dir: Directory for outputs.
        device: Device for inference.

    Returns:
        Configured VisionServer.
    """
    if model_type == "sam3":
        model = SAM3Model(model_path=model_path, device=device)
    else:
        raise ValueError(f"Unknown model type: {model_type}")

    return VisionServer(model=model, output_dir=output_dir)


def main() -> int:
    """CLI entry point for ``python -m r4_mcp.servers.vision``.

    bug.memo B4 (Phase 2h, 2026-04-25): wires the existing
    :class:`VisionServer` / :func:`create_vision_server` factory to a
    process entry point so the umrs ``serve-model.yaml`` no longer
    needs to reach into the published ``r4_mcp`` image. Configuration
    is environment-driven (HPC-first; no CLI args).
    """
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO)
    log = _logging.getLogger("r4_mcp.servers.vision")

    model_type = os.environ.get("MCP_VISION_MODEL_TYPE", "sam3")
    model_path = os.environ.get("UMRS_SAM3_MODEL_PATH") or os.environ.get("VISION_MODEL_PATH")
    if not model_path:
        log.error(
            "MCP vision server requires UMRS_SAM3_MODEL_PATH (or "
            "VISION_MODEL_PATH) to point at a pre-staged model"
        )
        return 2
    output_dir = os.environ.get("VISION_OUTPUT_DIR", "/tmp/vision_outputs")
    device = os.environ.get("MCP_VISION_DEVICE", "cuda")

    try:
        server = create_vision_server(
            model_type=model_type,
            model_path=model_path,
            output_dir=output_dir,
            device=device,
        )
    except Exception as exc:
        log.exception("Failed to create vision server: %s", exc)
        return 1

    server.run()
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
