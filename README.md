# Title
ADR: ECS Fargate replacing ECS EC2

# Context
- In 2016, the team decided that the [Eportfolio](https://www.edtpa.com/PageView.aspx?f=GEN_Prepare.html) application's infrastructure will be build on an ECS cluster  - using self managed EC2 instances. The SRE team will be in-charge of managing the EC2 instances in this cluster. This includes provisioning the instances using terraform - setting up user-data script to configure the ecs agent, writting external script to manage the scaling of the instances in conjuction with auto-scaling, and manual/automated security patching of the intances.
- In 2017, AWS introduced Fargate, Fargate is a AWS managed cluster - meaning the SRE team will not have to manage and maintain the EC2 instances. The introductory price for ECS Fargate was expensive back then. Also, we only had 2 services running in the cluster. The team, decided to continue managing and supporting the EC2 instances for the ECS cluster.
- In 2020, AWS offered a special discount to Pearson to use AWS Fargate (AWS managed cluster). The number of services for Eportfolio also increased from 2 to 7. The number of task per service also increased significantly. 


# Decision 
- Managing the EC2 instances we trivial when the number of services were low. As the project got bigger, we found it difficult to scale the EC2 instances proportionally to the demand of the users. 
- In 2021, the cost of AWS ECS Fargate was slightly more expensive than AWS ECS EC2. But the labor cost of managing the EC2 instances were also high. It may pose pose a security risk if the SRE team does not patch or keep the EC2 instances up-to-date. 
- The SRE did a cost analysis comparing both options. SRE team presented the advantages and disadvantages to the development team.

| Considerations | AWS ECS Fargate | AWS ECS EC2 |
| ----------- | ----------- | ----------- |
| Additional script required to fine tune auto-scaling | No | Yes |
| Automated patching required | No | Yes |
| Additional security/networking configuration | No | Yes |
| Additional work required to add new services | Lower | Higher |
| Cost of operation | Higher | Lower |
| Labor cost | Lower | Higher |
| Flexibility | Lower | Higher |

### Cost analysis
#### User managed EC2 (prices in 2021)
- EC2 = USD 0.80/hour
- m4.4xlarge
- 16 vCPU - 64 GiB

#### AWS managed  Fargate (prices in 2021)
- Fargate = USD 0.93216/hour (16.5% higher)
- 16 vCPU - > 16 * 0.04040 = 0.64768
- 64 GiB -> 64 * 0.004445 = 0.28448
- Total = 0.93216

### ESP Sandbox environment (October 2021)
- SRE team decided to launch a brand new environment to test the application on AWS Fargate - ESP sandbox. 
- On this environment, both SRE and developer can fine tune the system to match the performance in Production.
- SRE added a deployment job parallel to the main deployment pipeline.

### Python comparison tool
- SRE team developed a python script that would generate a report comparing the cost of the infrastucture if AWS Fargate were to be implemented
- [Source to tool](./python-tool/README.md) 

# Status 
- Proposed (April 2022)
- Since the implementation of AWS Fargate in our testing environment (ESP Sandbox) - the SRE team and developers are actively testing the application's performance in the AWS EC2 Fargate.implementation (April 2022).
- Operational cost and labor cost are still being tracked (October 2021 - April 2022).

Consequences 
- SRE and developer are working collaboratively to push the performance of the infrastucture.
- Things that are being considered are: Scalability, Latency, High Availability and Fault Tolerance of the system.
- As of April 2022, both SRE and developers find that it's harder to move from one working and proven platform to a unknown, unproven platform. Because of this, and due to our commitments to our SLA, we are testing the AWS Fargate to the fullest extend.
