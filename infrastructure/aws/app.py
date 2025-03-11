"""AWS CDK application for the stac-fastapi-geoparquet Stack

Generates a Lambda function with an API Gateway trigger and an S3 bucket.

After deploying the stack you will need to make sure the geoparquet file
specified in the config gets uploaded to the bucket associated with this stack!

Also includes a pgstac for side-by-side testing.
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
from aws_cdk.aws_ec2 import (
    GatewayVpcEndpointAwsService,
    InstanceType,
    InterfaceVpcEndpointAwsService,
    Peer,
    Port,
    SubnetConfiguration,
    SubnetSelection,
    SubnetType,
    Vpc,
)
from aws_cdk.aws_iam import AnyPrincipal, Effect, PolicyStatement
from aws_cdk.aws_lambda import Code, Function, Runtime
from aws_cdk.aws_logs import RetentionDays
from aws_cdk.aws_rds import DatabaseInstanceEngine, PostgresEngineVersion
from aws_cdk.aws_s3 import BlockPublicAccess, Bucket
from aws_cdk.custom_resources import (
    AwsCustomResource,
    AwsCustomResourcePolicy,
    AwsSdkCall,
    PhysicalResourceId,
)
from config import Config
from constructs import Construct
from eoapi_cdk import PgStacApiLambda, PgStacDatabase


class VpcStack(Stack):
    def __init__(
        self, scope: Construct, config: Config, id: str, **kwargs: Any
    ) -> None:
        super().__init__(scope, id=id, tags=config.tags, **kwargs)

        self.vpc = Vpc(
            self,
            "vpc",
            subnet_configuration=[
                SubnetConfiguration(
                    name="ingress", subnet_type=SubnetType.PUBLIC, cidr_mask=24
                ),
                SubnetConfiguration(
                    name="application",
                    subnet_type=SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
                SubnetConfiguration(
                    name="rds",
                    subnet_type=SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
            nat_gateways=config.nat_gateway_count,
        )
        self.vpc.add_interface_endpoint(
            "SecretsManagerEndpoint",
            service=InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
        )
        self.vpc.add_interface_endpoint(
            "CloudWatchEndpoint",
            service=InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
        )
        self.vpc.add_gateway_endpoint("S3", service=GatewayVpcEndpointAwsService.S3)
        self.export_value(
            self.vpc.select_subnets(subnet_type=SubnetType.PUBLIC).subnets[0].subnet_id
        )
        self.export_value(
            self.vpc.select_subnets(subnet_type=SubnetType.PUBLIC).subnets[1].subnet_id
        )


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


class StacFastApiPgstacStack(Stack):
    def __init__(
        self,
        scope: Construct,
        vpc: Vpc,
        id: str,
        config: Config,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            scope,
            id=id,
            tags=config.tags,
            **kwargs,
        )
        pgstac_db = PgStacDatabase(
            self,
            "pgstac-db",
            vpc=vpc,
            engine=DatabaseInstanceEngine.postgres(
                version=PostgresEngineVersion.VER_16
            ),
            vpc_subnets=SubnetSelection(subnet_type=(SubnetType.PUBLIC)),
            allocated_storage=config.pgstac_db_allocated_storage,
            instance_type=InstanceType(config.pgstac_db_instance_type),
            removal_policy=RemovalPolicy.DESTROY,
        )
        # allow connections from any ipv4 to pgbouncer instance security group
        assert pgstac_db.security_group
        pgstac_db.security_group.add_ingress_rule(Peer.any_ipv4(), Port.tcp(5432))
        pgstac_api = PgStacApiLambda(
            self,
            "stac-api",
            api_env={
                "NAME": "stac-fastapi-pgstac",
                "description": f"{config.stage} STAC API",
            },
            db=pgstac_db.connection_target,
            db_secret=pgstac_db.pgstac_secret,
            stac_api_domain_name=None,
        )

        assert pgstac_api.url
        CfnOutput(self, "ApiURL", value=pgstac_api.url)


app = App()
config = Config()
vpc_stack = VpcStack(scope=app, config=config, id=f"vpc-{config.name}")
StacFastApiPgstacStack(
    scope=app, vpc=vpc_stack.vpc, config=config, id=f"{config.name}-pgstac"
)
StacFastApiGeoparquetStack(
    app,
    config.stack_name,
    config=config,
)
app.synth()
