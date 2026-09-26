---
license: apache-2.0
library_name: transformers
pipeline_tag: image-segmentation
tags:
- flair-one
- aerial image
---

# pretrained model

- https://huggingface.co/nvidia/mit-b0
- SegFormer (b0-sized) encoder pre-trained-only
- SegFormer encoder fine-tuned on Imagenet-1k. It was introduced in the paper SegFormer: Simple and Efficient Design for Semantic Segmentation with Transformers by Xie et al. and first released in this repository.


# training-set
- you can find the training-set here : https://codalab.lisn.upsaclay.fr/competitions/8769

# training-arguments
- channels : rgb
- batch : 8
- epochs : 8
- learning-rate : 5e-6
- GPU : T4

# results on test-set
- Mean IoU : 59.9 
- more information here : https://codalab.lisn.upsaclay.fr/competitions/8769