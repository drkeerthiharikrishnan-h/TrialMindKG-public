TrialMindKG runs on AWS in Region ap-southeast-2 (Sydney) and calls NVIDIA NIM hosted models; NVIDIA parts sit outside the AWS Cloud. Badges mark each part LIVE, ON DEMAND or BUILD TIME; dashed arrows are build-time steps.
1. A visitor reaches Caddy on the EC2 instance through Amazon CloudFront, or through a second, direct HTTPS address.
2. Caddy passes requests to the FastAPI container, which serves pages, cards, search and evidence from the SQLite card index.
3. FastAPI calls NVIDIA NIM chat models to turn a typed question into validated openCypher, summarise answers and check sentences.
4. On request, FastAPI runs openCypher on Amazon Neptune inside the VPC; a timer wakes Neptune if it is stopped.
5. At boot the instance reads the NVIDIA key from Parameter Store and syncs the bundle, data and index parts from Amazon S3.
6. A CloudWatch alarm and a 30-minute EventBridge Scheduler sweep invoke Lambda to stop idle Neptune; a nightly schedule also stops it.
7. The EC2 status alarm e-mails the owner through Amazon SNS; AWS Budgets e-mails the owner directly.
8. The owner's Mac uploads files to S3; the Neptune bulk loader reads them through the VPC endpoint, and Run Command runs the load checks.
9. The owner's Mac launches the instance with user data; IAM roles give each part only its own permissions.
10. The owner's Mac sends graphs to an NVIDIA GPU host for link-prediction runs and calls NIM embed, rerank and rater models.
