# CAD SIS 3 Payment Service

An SNS payment event is delivered as raw JSON to an SQS standard queue. An ECS Fargate worker consumes the event in a private subnet. A DynamoDB transaction inserts the unique payment ID and credits the account. The worker acknowledges a message only after the transaction succeeds or confirms that the same payment was already applied. Invalid messages remain unacknowledged and move to the DLQ after five receives.

The same ECS task runs an HTTP container behind an Application Load Balancer. Its /health endpoint supports ALB checks. The task execution role pulls images and writes logs. The separate task role can read the payment queue and update the two ledger tables. CloudWatch keeps application and worker logs for seven days.

## Local tests

~~~bash
cd app
python3 -m pip install -r requirements.txt
python3 -m unittest -v
~~~

## Deploy from a clean checkout

In AWS CloudShell with Docker and AWS deployment permissions, choose an unused lowercase stack prefix. The deployment script creates the network, image repository, messaging resources and ECS service, then checks /health. ECR tags are immutable and its lifecycle policy keeps the ten newest images.

~~~bash
bash scripts/deploy.sh cad-sis3-demo
~~~

The stack names are cad-sis3-demo-network, cad-sis3-demo-images, cad-sis3-demo-messaging and cad-sis3-demo-app. The default deployment region is us-east-1. Set AWS_REGION before running the command to select another region. You can pass an explicit image tag as the second argument. The default is the checked-out Git commit SHA.

The course deployment already has cad-sis3-network and sis3-payment-images. To update it without recreating those stacks, use:

~~~bash
SIS3_SHARED_BASE=1 SIS3_IMAGE_STACK=sis3-payment-images \
  SIS3_APP_STACK=cad-sis3-app-v2 bash scripts/deploy.sh cad-sis3
~~~

## Integration verification

~~~bash
python3 scripts/smoke.py
~~~

For a fresh demo deployment, pass --messaging-stack cad-sis3-demo-messaging and --app-stack cad-sis3-demo-app. The test uses a unique synthetic account each time. It credits 100, resends the same payment ID, sends a second payment of 50, and verifies two processed IDs, a final balance of 150, and a duplicate_skipped log entry. It then verifies that the invalid event reached the DLQ. The test leaves the event available for inspection.

## Practice 7

A push to main runs the tests, authenticates to AWS using GitHub OIDC and the production environment, pushes the image with the commit SHA, updates the course ECS stack through CloudFormation, and checks the active image and /health. The production environment must define DEPLOY_ROLE_ARN. The deploy role assumes the named course deployment already exists.

## Remove a demo deployment

Delete the app stack first, then its messaging and network stacks. For a dedicated image repository, remove its demo images before deleting the image stack. Remove only stacks created with the demo prefix. NAT, ALB and Fargate can incur charges while the deployment runs.
