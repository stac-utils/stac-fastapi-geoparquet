"""AWS CDK application for the stac-fastapi-geoparquet Stack

Generates a Lambda function with an API Gateway trigger and an S3 bucket.

After deploying the stack you will need to make sure the geoparquet file
specified in the config gets uploaded to the bucket associated with this stack!
"""

import os
from typing import Any

from aws_cdk import (
    App,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
)
from aws_cdk.aws_apigatewayv2 import HttpApi, HttpStage, ThrottleSettings
from aws_cdk.aws_apigatewayv2_integrations import HttpLambdaIntegration
from aws_cdk.aws_iam import AnyPrincipal, Effect, PolicyStatement
from aws_cdk.aws_lambda import Code, Function, Runtime
from aws_cdk.aws_logs import RetentionDays
from aws_cdk.aws_s3 import BlockPublicAccess, Bucket
from aws_cdk.custom_resources import (
    AwsCustomResource,
    AwsCustomResourcePolicy,
    AwsSdkCall,
    PhysicalResourceId,
)
from config import Config
from constructs import Construct


class StacFastApiGeoparquetStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config: Config,
        runtime: Runtime = Runtime.PYTHON_3_12,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        for key, value in config.tags.items():
            Tags.of(self).add(key, value)

        bucket = Bucket(
            scope=self,
            id="bucket",
            bucket_name=config.bucket_name,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN
            if config.stage != "test"
            else RemovalPolicy.DESTROY,
            public_read_access=True,
            block_public_access=BlockPublicAccess(
                block_public_acls=False,
                block_public_policy=False,
                ignore_public_acls=False,
                restrict_public_buckets=False,
            ),
        )

        # make the bucket public, requester-pays
        bucket.add_to_resource_policy(
            PolicyStatement(
                actions=["s3:GetObject"],
                resources=[bucket.arn_for_objects("*")],
                principals=[AnyPrincipal()],
                effect=Effect.ALLOW,
            )
        )

        add_request_pay = AwsSdkCall(
            action="putBucketRequestPayment",
            service="S3",
            region=self.region,
            parameters={
                "Bucket": bucket.bucket_name,
                "RequestPaymentConfiguration": {"Payer": "Requester"},
            },
            physical_resource_id=PhysicalResourceId.of(bucket.bucket_name),
        )

        aws_custom_resource = AwsCustomResource(
            self,
            "RequesterPaysCustomResource",
            policy=AwsCustomResourcePolicy.from_sdk_calls(
                resources=[bucket.bucket_arn]
            ),
            on_create=add_request_pay,
            on_update=add_request_pay,
        )

        aws_custom_resource.node.add_dependency(bucket)

        CfnOutput(self, "BucketName", value=bucket.bucket_name)

        api_lambda = Function(
            scope=self,
            id="lambda",
            runtime=runtime,
            handler="handler.handler",
            memory_size=config.memory,
            log_retention=RetentionDays.ONE_WEEK,
            timeout=Duration.seconds(config.timeout),
            code=Code.from_docker_build(
                path=os.path.abspath("../.."),
                file="infrastructure/aws/lambda/Dockerfile",
                build_args={
                    "PYTHON_VERSION": runtime.to_string().replace("python", ""),
                },
            ),
            environment={
                "STAC_FASTAPI_GEOPARQUET_HREF": f"s3://{bucket.bucket_name}/{config.geoparquet_key}",
                "HOME": "/tmp",  # for duckdb's home_directory
            },
        )

        bucket.grant_read(api_lambda)

        api = HttpApi(
            scope=self,
            id="api",
            default_integration=HttpLambdaIntegration(
                "api-integration",
                handler=api_lambda,
            ),
            default_domain_mapping=None,  # TODO: enable custom domain name
            create_default_stage=False,  # Important: disable default stage creation
        )

        stage = HttpStage(
            self,
            "api-stage",
            http_api=api,
            auto_deploy=True,
            stage_name="$default",
            throttle=ThrottleSettings(
                rate_limit=config.rate_limit,
                burst_limit=config.rate_limit * 2,
            )
            if config.rate_limit
            else None,
        )

        assert stage.url
        CfnOutput(self, "ApiURL", value=stage.url)


app = App()
config = Config()
StacFastApiGeoparquetStack(
    app,
    config.stack_name,
    config=config,
)
app.synth()
